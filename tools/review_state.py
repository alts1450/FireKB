"""Content-bound human and AI decisions shared by verification and exports."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

AI_WEIGHTS_V1={'coverage':0.35,'authority':0.25,'independence':0.15,'applicability':0.15,'agreement':0.10}
AI_WEIGHTS={'coverage':0.45,'authority':0.20,'independence':0.05,'applicability':0.20,'agreement':0.10}
AI_POLICY='ai-web-v2'
AI_THRESHOLD=0.80


def ai_score(components, policy=AI_POLICY):
    # Decimal avoids rejecting an exact threshold through binary rounding.
    weights=AI_WEIGHTS_V1 if policy=='ai-web-v1' else AI_WEIGHTS
    return float(sum(Decimal(str(weight))*Decimal(str(components[key])) for key,weight in weights.items()))


def ai_approved(receipt, current_revision):
    try:
        if not isinstance(receipt,dict) or receipt.get('policy') not in {'ai-web-v1',AI_POLICY} or receipt.get('status')!='approved' or receipt.get('revision')!=current_revision:return False
        if datetime.fromisoformat(receipt['valid_until'])<=datetime.now(timezone.utc):return False
        values=receipt['components'];gates=receipt['gates']
        expected=({'primary_source','full_coverage','two_evidence_origins','same_scope'} if receipt['policy']=='ai-web-v1'
                  else {'full_coverage','web_corroboration','compatible_scope','standard_web_coverage'})
        expected|={f'{label}_{key}' for label in ['first','second'] for key in ['complete','definition_fully_supported','numbers_units_checked','formula_checked','scope_clear','no_conflict','all_claims_covered','scope']}
        if set(gates)!=expected or not all(v is True for v in gates.values()):return False
        if set(values)!=set(AI_WEIGHTS) or any(not isinstance(v,(int,float)) or not 0<=v<=1 for v in values.values()):return False
        score=ai_score(values,receipt['policy'])
        threshold=0.90 if receipt['policy']=='ai-web-v1' else AI_THRESHOLD
        return score>=threshold and receipt.get('score',0)>=threshold and abs(score-receipt['score'])<0.00011 and bool(receipt.get('sources'))
    except (KeyError,TypeError,ValueError,OverflowError):return False


def revision(kp, evidence, source=""):
    fields = ("name", "definition", "numbers", "note", "source_file", "page_label_start", "page_label_end")
    data = {k: kp.get(k) for k in fields}
    data["evidence"] = sorted([(e.get("evidence_id"), e.get("source_file"), e.get("page_label"), e.get("quote")) for e in evidence], key=str)
    data["source"] = source
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def human_label(kp):
    receipt=kp.get('human_review',{})
    return f"人工核验 · {receipt.get('at','')} · {receipt.get('by','')}" if kp.get('verification_method')=='human' else ''


def issue_key(row):
    return (row.get("kp_id"), row.get("type"), row.get("target"))


def evidence_accepted(evidence, current_revision):
    return bool(evidence.get('quote_verified') or (evidence.get('human_verified')
                and evidence.get('human_revision') == current_revision) or ai_approved(evidence.get('ai_verification'),current_revision))


def content_decision_accepted(kp, audits, current_revision):
    """AI can resolve numeric/semantic findings even when quotes already match."""
    issues=[a for a in audits if a.get('kp_id')==kp['kp_id'] and not a.get('superseded')
            and (a.get('priority')=='P0' or a.get('type')=='quote_partial')]
    if any(a.get('human_review',{}).get('verdict')=='bad' for a in issues):return False
    if kp.get('verification_method')=='human':
        receipt=kp.get('human_review',{})
        return receipt.get('verdict')=='ok' and receipt.get('revision')==current_revision
    if kp.get('verification_method')!='ai_web':return True
    if ai_approved(kp.get('ai_verification'),current_revision):return True
    # A later complete human review can replace an expired AI decision.
    return bool(issues) and all(a.get('revision')==current_revision and a.get('human_review',{}).get('verdict')=='ok' for a in issues)


def reconcile(new, old, selected_ids):
    """Preserve other courses and history; reuse decisions only on exact content."""
    index = {(issue_key(a), a.get("revision")): a for a in old}
    for a in new:
        prev = index.get((issue_key(a), a.get("revision")))
        if prev:
            for field in ("audit_id", "status", "human_review", "created_at"):
                if field in prev:
                    a[field] = prev[field]
            if prev.get('ai_review'):
                if ai_approved(prev['ai_review'],a.get('revision')):a['ai_review']=prev['ai_review']
                elif prev.get('human_review',{}).get('verdict') not in {'ok','bad'}:a['status']='pending'
    live = {a["audit_id"] for a in new}
    history = []
    for a in old:
        if a.get("kp_id") not in selected_ids:
            history.append(a)
        elif a.get("audit_id") not in live:
            a = dict(a)
            a["status"] = "ignored"
            a["superseded"] = True
            history.append(a)
    return history + new


def kp_status(kp_id, audits):
    current = [a for a in audits if a.get("kp_id") == kp_id and not a.get("superseded")]
    if any(a.get("human_review", {}).get("verdict") == "bad" for a in current):
        return "rejected"
    blocked = any((a.get("priority") == "P0" or a.get('type') == 'quote_partial')
                  and (a.get("status", "pending") == "pending" or
                       (a.get('ai_review') and not ai_approved(a['ai_review'],a.get('revision'))
                        and a.get('human_review',{}).get('verdict')!='ok')) for a in current)
    return "pending" if blocked else "verified"
