"""Search/fetch with the existing DeepSeek credential; no key reaches source sites."""
import hashlib
import json
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from threading import Event
import kb
import llm
from review_budget import WEB_REQUEST
from engines import egress

_blocked=Event()


class WebUnavailable(RuntimeError):
    """Configuration or balance prevents this run from obtaining web evidence."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError('联网 API 重定向被拒绝')


def call(operation, payload, budget=None):
    cfg=llm.load_env()
    base=egress.normalize_recipient(cfg.get('DEEPSEEK_BASE_URL') or llm.DEFAULT_BASE)
    if urlsplit(base).hostname!='api.deepseek.com':
        raise RuntimeError('当前端点不支持 DeepSeek Search/Fetch，请在模型配置中使用官方端点')
    egress.require_recipient(kb.CONFIG_DIR,'page_text',base,'firekb_knowledge_processing')
    if operation not in {'search','fetch'}: raise ValueError('未知联网操作')
    cache=kb.LOG_DIR/'web-review/cache'/(hashlib.sha256(json.dumps([operation,payload],sort_keys=True,ensure_ascii=False).encode()).hexdigest()+'.json')
    saved=kb.load_json(cache,{})
    if time.time()-saved.get('timestamp',0)<7*86400:return saved['response']
    if _blocked.is_set():raise WebUnavailable('本轮联网 API 已停止，请处理余额/认证后续跑')
    url=base.removesuffix('/v1')+'/v1/tools/'+operation
    request=Request(url,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+cfg['DEEPSEEK_API_KEY'],'Content-Type':'application/json'},method='POST')
    for attempt in range(3):
        reservation=budget.reserve(WEB_REQUEST) if budget else None
        try:
            with build_opener(NoRedirect()).open(request,timeout=90) as resp:
                result=json.loads(resp.read(8_000_000).decode('utf-8'))
            kb.write_json(cache,{'timestamp':time.time(),'response':result})
            if budget:budget.settle(reservation,WEB_REQUEST)
            return result
        except HTTPError as exc:
            if budget:budget.settle(reservation,0)
            if exc.code in {401,402,403}:
                _blocked.set()
                reason='余额不足（Insufficient Balance）' if exc.code==402 else '认证或权限不可用'
                raise WebUnavailable(f'DeepSeek {operation} HTTP {exc.code}：{reason}，已停止本轮新请求') from None
            if exc.code not in {408,429,500,502,503,504} or attempt==2:
                raise RuntimeError(f'DeepSeek {operation} HTTP {exc.code}') from None
        except (TimeoutError,OSError):
            if budget:budget.settle(reservation)
            if attempt==2:raise
        time.sleep(2**attempt)


def search(query,budget=None):
    return call('search',{'query':query[:128],'count':8,'need_content':True},budget).get('results',[])


def fetch(url,budget=None):
    parsed=urlsplit(url)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('无效来源 URL')
    return call('fetch',{'url':url},budget)
