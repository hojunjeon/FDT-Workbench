"""Optional output adapter: self-contained, escaped HTML/SVG; no business calculations or network."""
from __future__ import annotations
import argparse
import html
import json
import math
from pathlib import Path
from fdt.engine import Engine
from fdt.util import read_json,validate


def esc(value) -> str:
    if value is None:return '—'
    if isinstance(value,(dict,list)):return html.escape(json.dumps(value,ensure_ascii=False))
    return html.escape(str(value))


def table(rows: list[dict], limit: int=100) -> str:
    if not rows:return '<p>데이터 없음</p>'
    fields=list(dict.fromkeys(k for row in rows for k in row))
    parts=['<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(k)+'</th>' for k in fields)+'</tr></thead><tbody>']
    for r in rows[:limit]:parts.append('<tr>'+''.join('<td>'+esc(r.get(k))+'</td>' for k in fields)+'</tr>')
    parts.append('</tbody></table></div>')
    if len(rows)>limit:parts.append(f'<p>{len(rows)}행 중 {limit}행 미리보기. 전체는 JSON 원본에 있습니다.</p>')
    return ''.join(parts)


def svg_chart(spec: dict, rows: list[dict]) -> str:
    if not rows:return '<p>데이터 없음</p>'
    yfields=spec['y'];fields=yfields+[spec[k] for k in ('lower','upper') if k in spec]
    values=[r.get(k) for r in rows for k in fields if isinstance(r.get(k),(int,float)) and not isinstance(r.get(k),bool)]
    if not values:return '<p>추가 입력이 필요하여 이 그래프의 수치가 null입니다.</p>'
    W,H,L,R,T,B=1000,340,95,60,25,70
    lo,hi=min(values),max(values)
    if spec['unit']=='probability':lo,hi=0,1
    elif spec['kind']=='bar':lo=min(lo,0);hi=max(hi,0)
    if lo==hi:lo-=1;hi+=1
    if spec['unit']!='probability':
        gap=(hi-lo)*.08;lo-=gap;hi+=gap
    def yy(v):return T+(hi-v)/(hi-lo)*(H-T-B)
    def xx(i):
        return L+(i+.5)/len(rows)*(W-L-R) if spec['kind']=='bar' else L+i/max(1,len(rows)-1)*(W-L-R)
    parts=[f'<svg role="img" aria-label="{esc(spec["title"])}" viewBox="0 0 {W} {H}">']
    for i in range(5):
        v=lo+(hi-lo)*i/4;y=yy(v)
        parts.append(f'<path d="M {L} {y:.1f} H {W-R}" fill="none" stroke="currentColor" opacity=".12"/>')
        label=f'{v:.0%}' if spec['unit']=='probability' else f'{v:,.0f}'
        parts.append(f'<text x="{L-12}" y="{y+4:.1f}" text-anchor="end">{esc(label)}</text>')
    indices=sorted({round(i*(len(rows)-1)/5) for i in range(6)})
    for i in indices:
        parts.append(f'<text x="{xx(i):.1f}" y="{H-B+28}" text-anchor="middle">{esc(rows[i].get(spec["x"]))}</text>')
    if spec['kind']=='scatter':
        xvals=[r.get(spec['x']) for r in rows if isinstance(r.get(spec['x']),(int,float))]
        xmin,xmax=min(xvals),max(xvals)
        if xmin==xmax:xmax=xmin+1
        # Replace categorical x labels with numeric range labels for the scatter.
        parts=[p for p in parts if not ('text-anchor="middle"' in p)]
        for i in range(6):
            v=xmin+(xmax-xmin)*i/5;x=L+(W-L-R)*i/5
            parts.append(f'<text x="{x:.1f}" y="{H-B+28}" text-anchor="middle">{v:,.0f}</text>')
        for r in rows:
            if r.get(spec['x']) is None or r.get(yfields[0]) is None:continue
            x=L+(r[spec['x']]-xmin)/(xmax-xmin)*(W-L-R);y=yy(r[yfields[0]])
            radius=6 if r.get('selected') else 3.5
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" fill="currentColor" opacity="{1 if r.get("feasible") else .25}"><title>{esc(r)}</title></circle>')
    else:
        if 'lower' in spec and 'upper' in spec:
            segments=[];current=[]
            for i,r in enumerate(rows):
                if r.get(spec['lower']) is None or r.get(spec['upper']) is None:
                    if current:segments.append(current);current=[]
                else:current.append((i,r))
            if current:segments.append(current)
            for segment in segments:
                points=[(xx(i),yy(r[spec['upper']])) for i,r in segment]+[(xx(i),yy(r[spec['lower']])) for i,r in reversed(segment)]
                parts.append('<polygon points="'+' '.join(f'{x:.2f},{y:.2f}' for x,y in points)+'" fill="currentColor" opacity=".12"/>')
        for j,k in enumerate(yfields):
            if spec['kind']=='bar':
                width=max(3,(W-L-R)/len(rows)*.55)
                for i,r in enumerate(rows):
                    if r.get(k) is None:continue
                    top=min(yy(r[k]),yy(0));height=max(1,abs(yy(r[k])-yy(0)))
                    parts.append(f'<rect x="{xx(i)-width/2:.2f}" y="{top:.2f}" width="{width:.2f}" height="{height:.2f}" fill="currentColor" opacity=".65"><title>{esc(r)}</title></rect>')
            else:
                path=[];connected=False
                for i,r in enumerate(rows):
                    if r.get(k) is None:connected=False;continue
                    path.append(f'{"L" if connected else "M"} {xx(i):.2f} {yy(r[k]):.2f}');connected=True
                dash=' stroke-dasharray="7 5"' if j%2 else ''
                parts.append(f'<path d="{" ".join(path)}" fill="none" stroke="currentColor" stroke-width="2"{dash}/>')
    parts.append('</svg><p class="legend">'+esc(' / '.join(f'{k} ({"점선" if j%2 else "실선"})' for j,k in enumerate(yfields)))+'</p>')
    return ''.join(parts)


def render(result: dict) -> str:
    validate('result',result);Engine.validate_visualizations(result)
    parts=['''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>FDT Engine — numeric output review</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;line-height:1.6}main{max-width:1180px;margin:40px auto;padding:0 28px}
h1{font-size:34px;margin-bottom:6px}h2{margin-top:0}section{border:1px solid;padding:24px;margin:24px 0;border-radius:12px}
.meta{opacity:.7}.note{border-left:4px solid;padding:12px 18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.metric{border:1px solid;padding:16px;overflow-wrap:anywhere}.metric strong{display:block;font-size:24px}.metric small{opacity:.7}
.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid;padding:9px 12px;text-align:left;white-space:nowrap}
svg{width:100%;height:auto}svg text{font-family:system-ui,sans-serif;font-size:12px;fill:currentColor}.legend{font-size:12px;overflow-wrap:anywhere}
pre{overflow:auto;font-size:12px}details{margin:14px 0}footer{margin:40px 0;font-size:12px;opacity:.7}
</style></head><body><main>''']
    parts.append('<h1>FDT / '+esc(result['mode'])+'</h1>')
    parts.append('<p class="meta">기준일 '+esc(result['as_of'])+' · '+str(result['horizon_days'])+'일 · '+str(result['model']['paths'])+' paths · '+esc(result['status'])+'</p>')
    parts.append('<p class="note">정형 엔진 결과의 검토용 어댑터입니다. 데모 잔액은 USER_ASSUMPTION이며 실제 재무상태가 아닙니다. 확률은 검증되지 않은 조건부 모델 값입니다.</p>')
    parts.append('<div class="grid">')
    for k,m in list(result['metrics'].items())[:8]:
        v=m['value'];text='알 수 없음' if v is None else f'{v:.1%}' if m['unit']=='probability' else f'{v:,.0f}'
        parts.append('<div class="metric"><small>'+esc(k)+'</small><strong>'+esc(text)+'</strong><span>'+esc(m['unit'])+'</span></div>')
    parts.append('</div>')
    for spec in result['visualizations']:
        rows=result['datasets'][spec['dataset']]
        parts.append('<section><h2>'+esc(spec['title'])+'</h2>')
        parts.append(table(rows) if spec['kind']=='table' else svg_chart(spec,rows))
        parts.append('<p class="meta">'+esc(spec['note'])+'</p><details><summary>데이터 및 시각화 명세</summary>'+table(rows,30)+'<pre>'+esc(spec)+'</pre></details></section>')
    parts.append('<section><h2>Metrics</h2>'+table([{'metric':k,**v} for k,v in result['metrics'].items()])+'</section>')
    if 'decision' in result:parts.append('<section><h2>Decision (정형)</h2><pre>'+esc(json.dumps(result['decision'],ensure_ascii=False,indent=2))+'</pre></section>')
    parts.append('<section><h2>가정 / 경고 / 한계</h2>'+table(result['assumptions'])+table(result['warnings'])+''.join('<p>'+esc(x)+'</p>' for x in result['limitations'])+'</section>')
    parts.append('<footer>input_digest: '+esc(result['input_digest'])+'<br>외부 리소스·스크립트·은행 호출 없음. 원본 JSON이 수치의 기준입니다.</footer></main></body></html>')
    return ''.join(parts)


def main():
    p=argparse.ArgumentParser();p.add_argument('--result',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(render(read_json(a.result)),encoding='utf-8');print(out)

if __name__=='__main__':main()
