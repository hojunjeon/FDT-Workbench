from html.parser import HTMLParser
import copy
from fdt import Engine
from scripts.render_report import render,svg_chart

class Collector(HTMLParser):
    def __init__(self):super().__init__();self.tags=[]
    def handle_starttag(self,tag,attrs):self.tags.append(tag)


def test_report_structural_safety_and_svg(tiny):
    result=Engine(tiny).run({'mode':'forecast','paths':20,'horizon_days':7})
    report=render(result);p=Collector();p.feed(report)
    assert 'svg' in p.tags and 'table' in p.tags and 'script' not in p.tags
    assert 'Content-Security-Policy' in report


def test_html_escaped(tiny):
    r=Engine(tiny).run({'mode':'forecast','paths':20,'horizon_days':7})
    r['visualizations'][0]['title']='<script>alert(1)</script>'
    doc=render(r)
    assert '<script>' not in doc
    assert '&lt;script&gt;' in doc


def test_null_not_plotted_as_zero():
    spec={'title':'unknown','y':['p50'],'x':'date','unit':'KRW','kind':'line'}
    assert 'null' in svg_chart(spec,[{'date':'2026-09-07','p50':None}])


def test_probability_axis_bounds():
    spec={'title':'risk','y':['p'],'x':'date','unit':'probability','kind':'line'}
    chart=svg_chart(spec,[{'date':'1','p':0},{'date':'2','p':1}])
    assert '108%' not in chart and '-8%' not in chart
    assert '100%' in chart and '0%' in chart
