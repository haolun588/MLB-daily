"""HTTP 請求（GET／POST）、重試與錯誤處理。"""

import json
import sys
import time
import urllib.error
import urllib.request

from .config import GEMINI_RETRYABLE_HTTP, GEMINI_RETRY_AFTER_CAP, GEMINI_RETRY_DELAYS


def fetch_json(url, retries=3, delay=5):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"Error fetching {url} (attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def fetch_text(url, retries=3, delay=5):
    """與 fetch_json 相同的重試與 User-Agent，回傳回應文字（str）；全部失敗回傳 None。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read().decode('utf-8')
        except Exception as e:
            print(f"Error fetching {url} (attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(delay)
    return None


class ApiError(Exception):
    """API 抓取失敗，或回應缺少預期結構（與「確實無賽事」不同）。"""


# ---------------------------------------------------------------- Gemini 翻譯
def _redact(text, secrets):
    for sec in secrets:
        text = text.replace(sec, "***")
    return text


def _http_error_detail(err, secrets):
    """讀取 HTTPError body，若為 Google 錯誤 JSON 回傳 ' status=... message=...'（遮蔽金鑰、message 前 200 字），否則空字串。"""
    try:
        body = json.loads(err.read().decode("utf-8", errors="replace"))
        info = body.get("error") if isinstance(body, dict) else None
        if not isinstance(info, dict):
            return ""
        status = _redact(str(info.get("status", "")), secrets)[:100]
        message = _redact(str(info.get("message", "")), secrets)[:200]
        return f" status={status} message={message}"
    except Exception:
        return ""

# 最後一次 post_json 的結果狀態：retryable=True 表示失敗屬於可重試類型（429/5xx/逾時/連線錯誤）且重試已耗盡；
# 不可重試（其他 4xx、解析失敗）或成功時為 False。呼叫端據此決定是否改用備用模型。
LAST_POST = {"retryable": False}


def _retry_after_seconds(err):
    try:
        value = err.headers.get("Retry-After") if err.headers else None
        return float(value) if value is not None and str(value).strip().replace(".", "", 1).isdigit() else None
    except Exception:
        return None


def post_json(url, payload, headers, timeout=30):
    """以標準函式庫 POST JSON，回傳解析後的 dict；失敗回傳 None。
    可重試錯誤（HTTP 429/500/502/503/504、逾時、連線錯誤）最多共嘗試 4 次，間隔 GEMINI_RETRY_DELAYS，
    並尊重數字型 Retry-After（取較大者，上限 120 秒）；其他錯誤不重試。
    結束時將 LAST_POST["retryable"] 設為「是否因可重試錯誤耗盡而失敗」。
    錯誤訊息只印 HTTP 狀態碼、例外類型，以及 Google 錯誤 JSON 的 error.status／error.message（前 200 字，
    且先把金鑰字串遮成 ***）；不印網址與 header。"""
    secrets = [v for k, v in (headers or {}).items() if "key" in k.lower() and isinstance(v, str) and v]
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    total = len(GEMINI_RETRY_DELAYS) + 1
    LAST_POST["retryable"] = False
    for attempt in range(total):
        retry_after = None
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            LAST_POST["retryable"] = False
            return result if isinstance(result, dict) else None
        except urllib.error.HTTPError as e:
            detail = _http_error_detail(e, secrets)
            print(f"Warning: translation request failed (attempt {attempt + 1}/{total}): HTTP {e.code}{detail}", file=sys.stderr)
            if e.code not in GEMINI_RETRYABLE_HTTP:
                LAST_POST["retryable"] = False
                return None
            retry_after = _retry_after_seconds(e)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # socket.timeout 為 TimeoutError 別名
            print(f"Warning: translation request failed (attempt {attempt + 1}/{total}): {type(e).__name__}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: translation request failed (attempt {attempt + 1}/{total}): {type(e).__name__}", file=sys.stderr)
            LAST_POST["retryable"] = False
            return None
        LAST_POST["retryable"] = True
        if attempt < total - 1:
            delay = GEMINI_RETRY_DELAYS[attempt]
            if retry_after is not None:
                delay = min(max(delay, retry_after), GEMINI_RETRY_AFTER_CAP)
            time.sleep(delay)
    return None
