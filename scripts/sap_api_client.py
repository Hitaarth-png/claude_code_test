"""Generic SAP API client.

Calls SAP OData services (e.g. S/4HANA, SAP Gateway, SAP BTP) over HTTPS.
Supports basic auth and OAuth 2.0 client-credentials, and handles the
X-CSRF-Token exchange that SAP requires for modifying requests.

Configuration is read from environment variables so no credentials live
in the code:

    SAP_BASE_URL        e.g. https://my-system.s4hana.ondemand.com
    SAP_AUTH_METHOD     "basic" (default) or "oauth"
    SAP_USERNAME        basic auth user
    SAP_PASSWORD        basic auth password
    SAP_TOKEN_URL       OAuth token endpoint (oauth only)
    SAP_CLIENT_ID       OAuth client id (oauth only)
    SAP_CLIENT_SECRET   OAuth client secret (oauth only)

Usage examples:

    # List entities from an OData service
    python sap_api_client.py get "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner" --top 10

    # Fetch a single entity with a filter
    python sap_api_client.py get "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product" \
        --filter "ProductType eq 'FERT'" --select "Product,ProductType"

    # Create an entity from a JSON payload file
    python sap_api_client.py post "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product" --data payload.json
"""

import argparse
import json
import os
import sys
import time

import requests


class SapApiError(Exception):
    """Raised when the SAP API returns an error response."""


class SapClient:
    def __init__(self, base_url, auth_method="basic", timeout=30, max_retries=3):
        self.base_url = base_url.rstrip("/")
        self.auth_method = auth_method
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._token_expiry = 0

        if auth_method == "basic":
            user = os.environ.get("SAP_USERNAME")
            password = os.environ.get("SAP_PASSWORD")
            if not user or not password:
                raise SapApiError("SAP_USERNAME and SAP_PASSWORD must be set for basic auth")
            self.session.auth = (user, password)
        elif auth_method == "oauth":
            self._refresh_oauth_token()
        else:
            raise SapApiError(f"Unknown auth method: {auth_method}")

    def _refresh_oauth_token(self):
        token_url = os.environ.get("SAP_TOKEN_URL")
        client_id = os.environ.get("SAP_CLIENT_ID")
        client_secret = os.environ.get("SAP_CLIENT_SECRET")
        if not all([token_url, client_id, client_secret]):
            raise SapApiError(
                "SAP_TOKEN_URL, SAP_CLIENT_ID and SAP_CLIENT_SECRET must be set for oauth"
            )
        resp = requests.post(
            token_url,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise SapApiError(f"Token request failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        self.session.headers["Authorization"] = f"Bearer {payload['access_token']}"
        # Refresh a minute before actual expiry to avoid mid-request rejection
        self._token_expiry = time.time() + payload.get("expires_in", 3600) - 60

    def _ensure_token(self):
        if self.auth_method == "oauth" and time.time() >= self._token_expiry:
            self._refresh_oauth_token()

    def _fetch_csrf_token(self, path):
        """SAP requires a CSRF token, fetched via GET, for POST/PUT/PATCH/DELETE."""
        resp = self.session.get(
            self.base_url + path,
            headers={"X-CSRF-Token": "Fetch"},
            timeout=self.timeout,
        )
        token = resp.headers.get("X-CSRF-Token")
        if not token:
            raise SapApiError("Server did not return an X-CSRF-Token")
        return token

    def request(self, method, path, params=None, body=None):
        self._ensure_token()
        headers = {}
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            headers["X-CSRF-Token"] = self._fetch_csrf_token(path)
            headers["Content-Type"] = "application/json"

        url = self.base_url + path
        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.ConnectionError as exc:
                last_error = exc
                time.sleep(2**attempt)
                continue

            # Retry on throttling and transient server errors
            if resp.status_code in (429, 502, 503, 504):
                last_error = SapApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                time.sleep(2**attempt)
                continue

            if resp.status_code >= 400:
                raise SapApiError(f"HTTP {resp.status_code}: {resp.text}")

            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

        raise SapApiError(f"Request failed after {self.max_retries} attempts: {last_error}")

    def get(self, path, top=None, skip=None, filter_=None, select=None, expand=None):
        """GET an OData collection or entity with common query options."""
        params = {}
        if top is not None:
            params["$top"] = top
        if skip is not None:
            params["$skip"] = skip
        if filter_:
            params["$filter"] = filter_
        if select:
            params["$select"] = select
        if expand:
            params["$expand"] = expand
        return self.request("GET", path, params=params)

    def post(self, path, body):
        return self.request("POST", path, body=body)

    def patch(self, path, body):
        return self.request("PATCH", path, body=body)

    def delete(self, path):
        return self.request("DELETE", path)


def main():
    parser = argparse.ArgumentParser(description="Call an SAP OData API")
    parser.add_argument("method", choices=["get", "post", "patch", "delete"])
    parser.add_argument("path", help="Service path, e.g. /sap/opu/odata/sap/API_PRODUCT_SRV/A_Product")
    parser.add_argument("--top", type=int, help="$top query option")
    parser.add_argument("--skip", type=int, help="$skip query option")
    parser.add_argument("--filter", dest="filter_", help="$filter query option")
    parser.add_argument("--select", help="$select query option")
    parser.add_argument("--expand", help="$expand query option")
    parser.add_argument("--data", help="Path to a JSON file with the request body")
    args = parser.parse_args()

    base_url = os.environ.get("SAP_BASE_URL")
    if not base_url:
        sys.exit("SAP_BASE_URL environment variable is not set")

    client = SapClient(base_url, auth_method=os.environ.get("SAP_AUTH_METHOD", "basic"))

    body = None
    if args.data:
        with open(args.data) as f:
            body = json.load(f)

    try:
        if args.method == "get":
            result = client.get(
                args.path,
                top=args.top,
                skip=args.skip,
                filter_=args.filter_,
                select=args.select,
                expand=args.expand,
            )
        elif args.method == "post":
            result = client.post(args.path, body)
        elif args.method == "patch":
            result = client.patch(args.path, body)
        else:
            result = client.delete(args.path)
    except SapApiError as exc:
        sys.exit(f"SAP API error: {exc}")

    if result is not None:
        json.dump(result, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
