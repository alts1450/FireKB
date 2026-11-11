"""Per-run conservative API cost guard for Flash web review (USD)."""
from threading import Lock
import math

# Official peak prices checked 2026-11-04. Never use cache discounts in reservations.
# https://api-docs.deepseek.com/quick_start/pricing/
INPUT_PER_MILLION=0.30
OUTPUT_PER_MILLION=1.20
WEB_REQUEST=0.00075
PRICE_DATE='2026-11-04'


class BudgetExceeded(RuntimeError):
    pass


class ReviewBudget:
    def __init__(self,limit):
        if not math.isfinite(limit) or limit<=0:raise ValueError('预算必须为有限正数')
        self.limit=float(limit);self.spent=0.0;self.reserved=0.0;self.calls=0;self.lock=Lock()

    def reserve(self,amount):
        with self.lock:
            if self.spent+self.reserved+amount>self.limit+1e-12:
                raise BudgetExceeded('本轮费用预算已不足，已停止新请求；可复用本地结果后续跑')
            self.reserved+=amount;self.calls+=1
        return amount

    def settle(self,reservation,actual=None):
        with self.lock:
            self.reserved-=reservation
            self.spent+=reservation if actual is None else max(0,float(actual))

    def snapshot(self):
        with self.lock:
            return {'limit_usd':self.limit,'estimated_spent_usd':round(self.spent,6),
                    'reserved_usd':round(self.reserved,6),'requests':self.calls,
                    'pricing_checked':PRICE_DATE,'note':'按官方峰时价估算，实际账单以服务商为准；价格变动可能影响估算上限'}


def chat_reservation(messages,max_tokens):
    # A UTF-8 byte count upper-bounds byte-based tokenizer input, plus framing margin.
    input_bound=sum(len(str(m['content']).encode('utf-8')) for m in messages)+2048
    return (input_bound*INPUT_PER_MILLION+max_tokens*OUTPUT_PER_MILLION)/1_000_000


def chat_cost(usage):
    if 'prompt_tokens' not in usage or 'completion_tokens' not in usage:return None
    return (usage['prompt_tokens']*INPUT_PER_MILLION+usage['completion_tokens']*OUTPUT_PER_MILLION)/1_000_000
