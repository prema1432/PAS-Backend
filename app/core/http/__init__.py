"""What a request tells us about its caller.

`ip` holds the IP primitives (extract, classify, version), `geolocation` turns a
public address into a city/country (best effort, never blocking a request) and
`user_agent` labels the browser, OS and device type. All of it feeds the
`login_events` audit log — nothing here is used for authorization.
"""
