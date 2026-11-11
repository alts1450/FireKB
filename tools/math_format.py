"""Conservative prose/math separation. Source evidence is never reformatted."""
import re
from html import escape

# Explicit layouts for expressions whose grouping is known; this is not an OCR guesser.
LAYOUTS = {
 'V≥(Qb-Qi)Th+Vx+Vs': r'V \geq (Q_b-Q_i)T_h+V_x+V_s',
 'h≥(P2+P4)/10': r'h \geq \frac{P_2+P_4}{10}',
 'D = √(4Q/(πV))': r'D=\sqrt{\frac{4Q}{\pi V}}',
 'Q = Q1 + Q2 + … + Qn = ΣQi': r'Q=Q_1+Q_2+\cdots+Q_n=\sum_{i=1}^{n}Q_i',
 'R=fLa+Lk':r'R=fL_a+L_k', 'Pxh=10(Hq+hd)':r'P_{xh}=10(H_q+h_d)',
 'Qf=N·q':r'Q_f=N\cdot q', 'R = f·Ld + cos45°·Sk':r'R=fL_d+\cos 45^{\circ}\cdot S_k',
 'L1 ≤ √(R² - (b/2)²)':r'L_1\leq\sqrt{R^2-\left(\frac{b}{2}\right)^2}',
 'Vx = 0.06 Qx T':r'V_x=0.06Q_xT',
 'V = 3.6(Σq_i t_i - q_b t_g)':r'V=3.6\left(\sum_i q_i t_i-q_b t_g\right)',
 'q=K√(10p)':r'q=K\sqrt{10p}', 'q = K √(10p)':r'q=K\sqrt{10p}',
 'Q设计=(1.15~1.30)Q理论':r'Q_{\mathrm{design}}=(1.15\sim1.30)Q_{\mathrm{theory}}',
 'N=SW/q':r'N=\frac{SW}{q}', 'q=K√(10p)':r'q=K\sqrt{10p}',
 'P = Pr + Pe + Ps':r'P=P_r+P_e+P_s', 'Q = Σqi':r'Q=\sum_{i=1}^{n}q_i',
 'V = Qs t':r'V=Q_st', 'n≥qgA/qc':r'n\geq\frac{q_g A}{q_c}',
 'Qh=α(Qh1+Qh2)=α(n1q1+n2q2)':r'Q_h=\alpha(Q_{h1}+Q_{h2})=\alpha(n_1q_1+n_2q_2)',
 'W=10⁻³f(n1q1t1+n2q2t2+V)':r'W=10^{-3}f(n_1q_1t_1+n_2q_2t_2+V)',
 'W=10⁻³(1-f)(n1q1t1+n2q2t2+V)':r'W=10^{-3}(1-f)(n_1q_1t_1+n_2q_2t_2+V)',
 'Q=1.2AsR':r'Q=1.2A_sR', 'D=(12~20)√Q':r'D=(12\sim20)\sqrt{Q}',
 'D=(8~16)√Q':r'D=(8\sim16)\sqrt{Q}', 'W_s = 1.1W':r'W_s=1.1W',
 'Q=K·S/U':r'Q=\frac{KS}{U}', 'Q=1.3K·S/U':r'Q=\frac{1.3KS}{U}',
 'Qe = Q / N':r'Q_e=\frac{Q}{N}', 'Q=N×Qi':r'Q=NQ_i',
 'Rf = f·Ld + Lk':r'R_f=fL_d+L_k', 'Hxh = Hq + hd + Hk':r'H_{xh}=H_q+h_d+H_k'
}


def format_text(text):
    """Idempotent, explicit layouts first; comparisons and simple equations second."""
    parts=re.split(r'(\$[^$]+\$)',text or '')
    for i in range(0,len(parts),2):
        s=parts[i]
        # Placeholders keep the generic pass away from known LaTeX expressions.
        saved=[]
        for raw,latex in sorted(LAYOUTS.items(),key=lambda pair:-len(pair[0])):
            if raw in s:
                key=f'§{len(saved)}§';saved.append('$'+latex+'$');s=s.replace(raw,key)
        pattern=r'(?<![A-Za-z])[A-Za-z][A-Za-z0-9_]*(?:\s*[=≥≤]\s*)[A-Za-z0-9_.+*/()^~×·²³⁻\s-]+'
        def simple(m):
            expr=m.group().rstrip(); tail=m.group()[len(expr):]
            expr=re.sub(r'([A-Za-z])([0-9]+)',r'\1_{\2}',expr)
            expr=re.sub(r'\^([0-9]+(?:\.[0-9]+)?)',r'^{\1}',expr)
            for a,b in [('≥',r'\geq '),('≤',r'\leq '),('×',r'\times '),('·',r'\cdot '),('²','^{2}'),('³','^{3}'),('~',r'\sim ')]:expr=expr.replace(a,b)
            return '$'+expr+'$'+tail
        s=re.sub(pattern,simple,s)
        for n,value in enumerate(saved):s=s.replace(f'§{n}§',value)
        parts[i]=s
    return ''.join(parts)


def display(kp):
    return format_text(kp.get('definition',''))


def html_text(text):
    return ''.join('<span class="math-tex">'+escape(p[1:-1])+'</span>' if p.startswith('$') else escape(p)
                   for p in re.split(r'(\$[^$]+\$)',text or '') if p)
