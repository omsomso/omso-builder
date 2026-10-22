"""omso Builder core.

Lists the checkouts of registered repositories, plans and runs their builds from
each checkout's omso-build.json, and installs finished builds. The builder knows
no products: every repository describes its own (docs/MANIFEST.md).
"""
