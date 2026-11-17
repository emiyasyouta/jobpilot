"""直接说 Chrome DevTools Protocol，不引 playwright。

为什么不用 playwright：它 34MB，走 CDP 只需要「打开一个标签页 → 等加载完 → 取 outerHTML」
这三步。为这三步装一个带浏览器下载器的框架，收益对不上成本。
改用 `websockets`（80KB，纯 Python）直接连 CDP：

    POST/GET /json/new?url=...     开标签页，拿到 webSocketDebuggerUrl
    Runtime.evaluate              执行 JS，等页面 readyState=complete，再取 outerHTML
    GET /json/close/<id>          关掉标签页

顺带的好处是「只读」这件事变得可验证：本模块只发 Runtime.evaluate 读 DOM，
没有任何 Input、Network 或 Page.navigate 之外的写操作，一眼就能看出它不会替你投递。
"""

from __future__ import annotations

import json
import time

import httpx

HTTP_TIMEOUT = 5.0

# 滚动到底再取 HTML：招聘站列表是懒加载的，只取首屏会稳定地少抓一半。
# 用「卡片数是否还在涨」判断停不停，而不是滚固定次数。
#
# 两处经验教训（都来自真实页面，不是猜的）：
# 1. readyState=complete 时 Vue 页面才刚挂上骨架，一张卡片都没有。旧版用
#    「卡片数不再增加就退出」，第一次循环 n=0、last=-1 不等，第二次 0===0 直接
#    break——总共只等了 700ms，抓回来的整页空骨架被当成「没有职位」。
#    所以阶段一必须先等目标选择器出现，等不到才认输。
# 2. 搜索页的 ?page=N 参数不生效（实测第 2 页和第 1 页内容一模一样），
#    翻页实际靠滚动加载。所以「翻页」在这里实现成「滚到卡片数达到目标」。


def _html_js(expect: str = "", target: int = 0) -> str:
    return f"""
(async () => {{
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const sel = {json.dumps(expect or "")};
  const target = {int(target)};

  const deadline = Date.now() + 15000;
  while (sel && Date.now() < deadline) {{
    if (document.querySelectorAll(sel).length > 0) break;
    await sleep(300);
  }}

  let last = -1, same = 0;
  for (let i = 0; i < 25; i++) {{
    const n = sel ? document.querySelectorAll(sel).length : 0;
    if (target > 0 && n >= target) break;
    if (n === last) {{ if (++same >= 2) break; }} else {{ same = 0; }}
    last = n;
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(900);
  }}
  window.scrollTo(0, 0);
  await sleep(300);
  return document.documentElement.outerHTML;
}})()
"""


class CDPError(RuntimeError):
    """CDP 调用失败。不区分细因——上层只需要知道「这次没拿到页面」。"""


def _client() -> httpx.Client:
    # trust_env=False：本机请求不能被系统代理接管，否则 127.0.0.1 会返回 502
    return httpx.Client(trust_env=False, timeout=HTTP_TIMEOUT)


def version(cdp_url: str) -> dict:
    """探活。返回浏览器版本信息，连不上直接抛异常。"""
    with _client() as c:
        r = c.get(f"{cdp_url.rstrip('/')}/json/version")
        r.raise_for_status()
        return r.json()


def _new_target(cdp_url: str, background: bool = True) -> dict:
    """开一个空白标签页。

    注意：不要指望 `/json/new?url=...` 帮你导航——Edge 实测会忽略 url 参数，
    开出来是 about:blank。所以导航统一走 CDP 的 Page.navigate，行为才可控。
    background=true 让新标签不抢用户焦点——抓取时不该把人正在看的东西切走。
    """
    path = "/json/new" + ("?background=true" if background else "")
    last = ""
    with _client() as c:
        for method in ("put", "get"):
            try:
                r = getattr(c, method)(f"{cdp_url.rstrip('/')}{path}")
                if r.status_code == 200:
                    return r.json()
                last = f"{method.upper()} {r.status_code}"
            except Exception as exc:  # noqa: BLE001
                last = f"{method.upper()} {type(exc).__name__}"
    raise CDPError(f"开标签页失败（{last}）。确认浏览器是用 --remote-debugging-port 启动的。")


def _close_target(cdp_url: str, target_id: str) -> None:
    if not target_id:
        return
    try:
        with _client() as c:
            c.get(f"{cdp_url.rstrip('/')}/json/close/{target_id}")
    except Exception:  # noqa: BLE001
        pass  # 关不掉不影响结果，最多留一个空标签


def _call(ws, mid: int, method: str, params: dict | None = None, timeout: float = 15.0):
    """发一条 CDP 命令并等它的响应，途中的事件通知直接丢弃。"""
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv(timeout=max(0.2, deadline - time.time()))
        except TimeoutError:
            break
        msg = json.loads(raw)
        if msg.get("id") != mid:
            continue
        if "error" in msg:
            raise CDPError(str(msg["error"].get("message", msg["error"])))
        return msg.get("result", {})
    raise CDPError(f"{method} 超时")


def _evaluate(ws, expr: str, mid: int, timeout: float, await_promise: bool = False):
    result = _call(ws, mid, "Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True,
        "awaitPromise": await_promise,
    }, timeout=timeout)
    if result.get("exceptionDetails"):
        desc = result["exceptionDetails"].get("text") or "JS 执行异常"
        raise CDPError(desc)
    return result.get("result", {}).get("value")


def _wait_loaded(ws, url: str, timeout: float) -> None:
    """等页面真的换成目标地址并加载完。

    不能只看 readyState：新标签页一开始就是 about:blank 且已经 complete，
    只判 readyState 会立刻返回一个空页面。所以同时核对 location.href。
    """
    want = url.split("?")[0]
    deadline = time.time() + timeout
    while time.time() < deadline:
        href = _evaluate(ws, "location.href", mid=901, timeout=5) or ""
        if href.startswith("http") and href.split("?")[0].startswith(want[:60]):
            if _evaluate(ws, "document.readyState", mid=902, timeout=5) == "complete":
                return
        time.sleep(0.4)
    raise CDPError("页面加载超时")


def capture_html(cdp_url: str, url: str, ready_timeout: float = 25.0,
                 render_timeout: float = 45.0, attempts: int = 2,
                 expect: str = "", target: int = 0) -> str:
    """打开 url，等渲染完，返回 outerHTML，然后关掉标签页。

    expect 给一个 CSS 选择器，表示「页面真的渲染好了」的判据（列表页 `li.job-card-box`，
    详情页 `.job-sec-text`）。不给就只等加载态，异步渲染可能还没完成。
    target 是希望凑够多少条（列表页滚动加载用），0 表示滚到不再增长为止。

    这是本模块唯一对外的动作。除了「开一个标签页、导航过去、读 DOM、关掉」，
    它不点任何按钮、不填任何表单。

    会重试的原因：招聘站在检测到异常时会把页面跳转到验证页，
    CDP 那边表现为 "Inspected target navigated or closed"。
    重试一次仍然跳转，就说明确实被拦了，交给上层提示人工处理。
    """
    last: CDPError | None = None
    for i in range(attempts):
        try:
            return _capture_once(cdp_url, url, ready_timeout, render_timeout, expect, target)
        except CDPError as exc:
            last = exc
            text = str(exc).lower()
            if "navigated" not in text and "closed" not in text:
                raise
            if i + 1 < attempts:
                time.sleep(1.5)
    raise last or CDPError("取页面失败")


def _capture_once(cdp_url: str, url: str, ready_timeout: float, render_timeout: float,
                  expect: str = "", target: int = 0) -> str:
    from websockets.sync.client import connect

    # 变量名别叫 target：那是上面传进来的「目标条数」，重名会把它覆盖成标签页对象
    tab = _new_target(cdp_url)
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        _close_target(cdp_url, tab.get("id", ""))
        raise CDPError("标签页没有返回 webSocketDebuggerUrl")

    try:
        # max_size=None：页面 HTML 动辄几 MB，默认 1MB 上限会把帧截断
        with connect(ws_url, max_size=None, open_timeout=10) as ws:
            _call(ws, 800, "Page.navigate", {"url": url}, timeout=ready_timeout)
            _wait_loaded(ws, url, ready_timeout)
            html = _evaluate(ws, _html_js(expect, target), mid=1, timeout=render_timeout,
                             await_promise=True)
            if not isinstance(html, str) or not html:
                raise CDPError("取到的页面内容为空")
            return html
    finally:
        _close_target(cdp_url, tab.get("id", ""))
