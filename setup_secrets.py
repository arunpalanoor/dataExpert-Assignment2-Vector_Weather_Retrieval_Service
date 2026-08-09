"""
One-time setup script: creates the Databricks secret scope. Run this locally (with the Databricks CLI configured) or
from a notebook - never commit the resulting secret value anywhere.
Add '&options=-csearch_path=your_schema_name' to Lakebase URL to set custom schema name.Lakebase defaults to public schema if no schema is specified in connection string.

Usage:
    python setup_secrets.py
"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace
import getpass

w = WorkspaceClient()

#w.secrets.create_scope(scope="weather")
w.secrets.put_secret(
    scope="weather",
    key="lakebase-url",
    string_value=getpass.getpass("Paste your Lakebase URL: ")
)

w.secrets.put_acl(
    scope="weather",
    principal="users",
    permission=workspace.AclPermission.READ,
)
