import requests

client_id = "7d251ac8-abad-44b8-85f4-2a75330c339c"
state = "eyJwIjp7ImNpZCI6MywidHMiOjE3NTk5NDc2ODR9LCJzIjoieTR5MnpCMHE5TEx5ZmxPM3BhendRaWE1dmxMUmQyM1VBOElfRWZRN05LQSJ9"
redirect = "https://4b701c742a92.ngrok-free.app/api/partners/company/3/crm/amocrm/callback"
params_oauth2 = {
    "client_id": client_id,
    "redirect_uri": redirect,
    "response_type": "code",
    "state": state,
}
params_oauth = {"client_id": client_id, "state": state, "mode": "post_message"}

def check(url, method="GET"):
    try:
        r = requests.request(method, url, allow_redirects=False, timeout=10)
        print(f"\nURL: {url}\nMETHOD: {method}\nSTATUS: {r.status_code}\nAllow: {r.headers.get('Allow')}\nHEADERS: {dict(r.headers)}")
        body = (r.text or "")[:1500]
        print("BODY PREVIEW:\n", body)
    except Exception as e:
        print("EXCEPTION for", url, e)

base = "https://healthclub35kz.amocrm.ru"
url_oauth2 = base + "/oauth2/authorize?" + "&".join([f"{k}={requests.utils.quote(v, safe='')}" for k,v in params_oauth2.items()])
url_oauth = base + "/oauth?" + "&".join([f"{k}={requests.utils.quote(v, safe='')}" for k,v in params_oauth.items()])

# Check GET /oauth2/authorize
check(url_oauth2, "GET")
# Check OPTIONS /oauth2/authorize
check(url_oauth2, "OPTIONS")
# Check POST /oauth2/authorize
check(url_oauth2, "POST")
# Check GET /oauth
check(url_oauth, "GET")
# Check OPTIONS /oauth
check(url_oauth, "OPTIONS")
# Check POST /oauth
check(url_oauth, "POST")
