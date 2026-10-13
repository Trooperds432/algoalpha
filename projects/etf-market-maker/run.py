"""Run all seven deliverables. Synthetic engineering capital only; no live mode."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np

from desk import (Limits, dislocation, dynamic_quote, fair_value, hedge_decision,
                  simulate, synthetic_events)
from flow_models import run_forecast_demo
from risk_governance import allocate_capital, risk_report, stress_report, validate_strategy

ROOT = Path(__file__).resolve().parent
FIRM = ROOT.parent.parent


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def feature_matrix(events):
    """Only event-time observations. Volatility uses a trailing NAV-return window."""
    rows, returns, previous = [], [], None
    for event in events:
        value = fair_value(event["basket"], event["timestamp"])["fair_value"]
        if previous is not None:
            returns.append(value / previous - 1)
        previous = value
        vol = float(np.std(returns[-30:], ddof=1)) if len(returns) >= 2 else 0.0002
        rows.append([event["observed_spread"], event["liquidity"], vol,
                     event["flow"]["order_imbalance"], event["flow"]["liquidity_depletion"]])
    return np.asarray(rows)


def forecast_schedule(events, model_report):
    schedule = [None] * len(events)
    for forecast in model_report["heldout_forecasts"]:
        decision, target = forecast["as_of_index"], forecast["target_index"]
        schedule[decision] = {"as_of": events[decision]["timestamp"],
                              "target_timestamp": events[target]["timestamp"],
                              "spread_width": forecast["spread_width"],
                              "liquidity": forecast["displayed_liquidity"],
                              "volatility": forecast["volatility"]}
    return schedule


def capacity_report(capital_gbp=100_000_000):
    """Illustrative ADV/turnover constraint, not evidence that GBP100m is feasible."""
    rows = []
    for adv in [50_000_000, 250_000_000, 1_000_000_000]:
        for turnover in [0.1, 1, 5]:
            volume = capital_gbp * turnover
            rows.append({"capital_gbp": capital_gbp, "daily_turnover": turnover,
                         "assumed_adv_gbp": adv, "daily_volume_gbp": volume,
                         "participation": volume / adv, "participation_cap": 0.05,
                         "within_volume_cap": volume <= 0.05 * adv})
    return {"target_capital_gbp": capital_gbp, "scenarios": rows, "approval": False,
            "reason": "No measured ADV, creation/redemption, borrow, queue, or hedge capacity evidence",
            "fx_assumption": "Scenario values already in GBP; no live FX conversion; USD simulation separate"}


def table(headers, rows):
    def cell(x):
        if isinstance(x, float):
            return f"{x:,.6g}"
        return html.escape(str(x))
    return '<div class="table"><table><thead><tr>' + ''.join(f'<th>{cell(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join(f'<td>{cell(c)}</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def report_html(report, output):
    summary, models = report["simulation"]["summary"], report["order_flow"]
    escaped = lambda x: html.escape(str(x))
    blocks = []
    blocks.append('<section id="fair"><h2>1 · Fair value before market price</h2>' + table(
        ['Fair value', 'Market price', 'Dislocation', 'Net edge/share'],
        [[report['fair_value'][k] for k in ['fair_value', 'market_price', 'dislocation', 'estimated_net_edge_per_share']]]) +
        '<p>Fixed-share basket valued from constituent prices and FX. The market price is supplied only after valuation. A deviation is a candidate, not guaranteed arbitrage.</p></section>')
    rows = [['Persistence', *[models['baseline']['test_mae'][t] for t in models['targets']]]]
    rows += [[name, *[v['test_mae'][t] for t in models['targets']]] for name, v in models['models'].items()]
    blocks.append('<section id="flow"><h2>2 · Order-flow forecasts</h2>' + table(['Model', 'Spread MAE', 'Liquidity MAE', 'Volatility MAE'], rows) +
                  '<p>Selected using validation only: <b>' + escaped(models['selection']['model']) + '</b>. All targets are next-event spread, depth and volatility; none predicts price. Forecasts drive held-out quotes; warm-up uses observed values.</p>' +
                  table(['Latest observed flow metric','Value'],list(models['current_observed_flow'].items())) + '</section>')
    hedge = report['inventory_example']
    blocks.append('<section id="inventory"><h2>3 · Inventory and hedge</h2>' + table(['ETF units', 'Hedge action', 'Basket units bought', 'Residual delta units'],
        [[hedge['inventory'], hedge['action'], hedge['signed_size'], hedge['residual_delta_units']]]) +
        '<p>This example has sufficient gross capacity and liquidity. The hedge leaves the short ETF position on the book. Actual demo fills remain bounded by separate net, gross and inventory limits.</p></section>')
    blocks.append('<section id="quotes"><h2>4 · Dynamic quotes</h2>' + table(['Condition', 'Bid', 'Ask', 'Width'],
        [[name, q['bid'], q['ask'], q['spread']] for name, q in report['quote_examples'].items()]) +
        '<p>Transparent inventory-skew and volatility/depth heuristic. Not a fitted optimal market-making policy. One-event quote latency, queue participation, fees and hedge slippage are simulated.</p></section>')
    risk = report['risk']
    blocks.append('<section id="risk"><h2>5 · Risk committee</h2>' + table(['Confidence', 'VaR USD', 'Expected shortfall USD', 'Tail observations'],
        [[level, risk['var'][level], risk['es'][level], risk['tail_uncertainty'][level]['observed_tail_count']] for level in ['95','99','99.9']]) +
        '<p>One-second synthetic return scenarios, fixed end-of-run exposures. 99.9% tail estimates are especially unstable. No conversion to daily VaR.</p>' +
        table(['Hypothetical stress', 'MTM PnL USD', 'Liquidation cost USD', 'Net PnL USD'],
              [[s['name'],s['mark_to_market_pnl'],s['liquidation_cost'],s['net_pnl']] for s in report['stress']['scenarios']]) +
        '<p>Named shocks are inspired by events; they are not replays. Liquidity shock triples the spread and halves depth. Both up and down shocks include hedge basis risk.</p></section>')
    reasons = ''.join('<li>' + escaped(r) + '</li>' for r in report['validation']['reasons'])
    blocks.append('<section id="validation"><h2>6 · Strategy Killer</h2><p class="reject">REJECTED for promotion</p><ul>' + reasons + '</ul>' +
        '<h3>Cost sensitivity</h3>' + table(['Scenario','Fee bps/leg','Hedge slippage bps','Net MTM PnL USD'],
        [[r['name'],r['fee_bps'],r['slippage_bps'],r['net_mtm_pnl']] for r in report['cost_sensitivity']]) +
        '<h3>GBP100m capacity scenarios</h3>' + table(['Daily turnover','Assumed ADV GBP','Participation','Within 5% cap'],
        [[r['daily_turnover'],r['assumed_adv_gbp'],r['participation'],r['within_volume_cap']] for r in report['capacity']['scenarios']]) +
        '<p>Capacity remains unproven. All failures are retained in this immutable run folder.</p></section>')
    allocation = report['capital']['allocations'][0]
    blocks.append('<section id="capital"><h2>7 · Capital allocation committee</h2>' + table(['Role','Vote'], list(allocation['votes'].items())) +
        '<p>Allocated strategy capital: <b>USD ' + escaped(report['capital']['allocated_capital']) + '</b>. Risk and Validation vetoes cannot be overridden. The USD1m used above is engineering simulation bookkeeping, not approval of a strategy.</p></section>')
    blocks.append('<section><h2>Desk run and reproducibility</h2>' + table(['Metric','Value'], list(summary.items())) +
        '<p>Seed ' + str(report['seed']) + ' · ' + str(report['event_count']) + ' synthetic one-second events · generated ' + escaped(report['created_at']) +
        '</p><p><a href="report.json">Full JSON</a> · <a href="fills.csv">Paper fills</a> · <a href="agent-messages.jsonl">Role handoffs</a> · <a href="manifest.json">Run manifest</a></p>' +
        '<p>Role handoffs are deterministic engine records linked to the Paperclip organisation. They are not fabricated LLM deliberations. Models and simulation are research software, not live market access.</p></section>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AlgoAlpha · ETF Market Maker</title>
<style>body{margin:0;background:#101822;color:#e7edf5;font:16px/1.55 system-ui}main{max-width:1150px;margin:auto;padding:40px 24px}h1{font-size:42px;line-height:1.15}h2{font-size:25px}a{color:#78caff}section{margin:26px 0;background:#172330;border:1px solid #304155;border-radius:12px;padding:24px}.tag{color:#5ee0b2;letter-spacing:.12em;font-size:13px}nav{display:flex;flex-wrap:wrap;gap:16px}.table{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #354457}th{color:#8db7cc}.reject{color:#ffba95;font-weight:bold}p{color:#bccbd9}li{margin:5px 0}footer{color:#99aabd;font-size:13px}</style>
<main><div class="tag">ALGOALPHA / PAPER RESEARCH DESK</div><h1>Multi-Agent ETF<br>Market Maker</h1><p>Engineering demo complete · Synthetic data · Strategy promotion blocked</p>
<nav><a href="#fair">Fair value</a><a href="#flow">Forecasts</a><a href="#inventory">Inventory</a><a href="#quotes">Quotes</a><a href="#risk">Risk</a><a href="#validation">Validation</a><a href="#capital">Capital</a></nav>'''
    page += ''.join(blocks) + '<footer>References: <a href="https://www.sec.gov/servlet/sec/investor/alerts/etfs.pdf">SEC ETF bulletin</a> · <a href="https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf">Avellaneda–Stoikov (2008)</a>. No claim to replicate an institutional firm or guarantee future performance.</footer></main></html>'
    (output / 'report.html').write_text(page, encoding='utf-8')


def run(seed=7, count=1000, epochs=10, phase='discovery', output_root=None):
    owner = read_json(FIRM / 'config/risk-thresholds.json')
    if owner.get('paper_trading_only') is not True or owner.get('live_trading_enabled') is not False:
        raise ValueError('Owner paper-only contract is not intact')
    threshold = owner['phases'][phase]
    limits = replace(Limits(), max_drawdown_pct=threshold['max_drawdown_pct'])
    created = datetime.now(timezone.utc)
    output = Path(output_root or ROOT / 'runs') / (created.strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    events = synthetic_events(seed, count)
    models = run_forecast_demo(seed=seed, epochs=epochs, data=feature_matrix(events), model_directory=output/'models')
    models['current_observed_flow'] = events[-1]['flow']
    forecasts = forecast_schedule(events, models)
    simulation = simulate(events, limits, seed=seed+1, forecasts=forecasts)
    end = simulation['quotes'][-1]
    valuations = np.array([q['fair_value'] for q in simulation['quotes']])
    markets = np.array([q['market_price'] for q in simulation['quotes']])
    returns = np.column_stack([np.diff(markets)/markets[:-1], np.diff(valuations)/valuations[:-1]])
    risk = risk_report([end['inventory']*markets[-1], end['hedge_units']*valuations[-1]], returns)
    risk['horizon'] = 'one synthetic second; no time scaling'
    stress = stress_report(end['inventory'], end['hedge_units'], markets[-1], valuations[-1],
                           events[-1]['observed_spread'], events[-1]['liquidity'])
    capacity = capacity_report()
    evidence = {'data_kind':'synthetic', 'historical_months':0, 'paper_days':0,
                'oos_documented':True, 'costs_documented':True, 'actual_paper_days_documented':False,
                'independent_backtest':False, 'independent_risk_review':False,
                'sharpe':None, 'max_drawdown':simulation['summary']['max_drawdown_pct']/100,
                'net_pnl_after_costs':simulation['summary']['net_mtm_pnl'], 'capacity_gbp':0,
                'feature_timestamps':list(range(count-1)), 'decision_timestamps':list(range(count-1)),
                'regimes':sorted({event['regime'] for event in events}),
                'benchmark':'No-edge cash baseline for engineering; real ETF total-return benchmark pending',
                'sharpe_convention':'Not estimated: short intraday synthetic sample',
                'fx_assumption':'USD desk; illustrative capacity scenarios denominated directly in GBP'}
    validation = validate_strategy(evidence, {'min_sharpe':threshold['sharpe_minimum'],
                                             'max_drawdown':threshold['max_drawdown_pct']/100})
    votes = {'quant':'buy' if simulation['summary']['net_mtm_pnl'] > 0 else 'neutral',
             'fundamental':'neutral', 'risk':'veto', 'execution':'buy',
             'validation':'approved' if validation['approval'] else 'rejected'}
    capital = allocate_capital([{'name':'Synthetic ETF market maker', 'requested_capital':250_000,
                                'votes':votes}], limits.initial_capital)
    costs = []
    for name, fee, slip in [('base',.5,1.0), ('higher_friction',2.0,5.0), ('severe_friction',10.0,20.0)]:
        result = simulation if name == 'base' else simulate(events, replace(limits, fee_bps=fee, slippage_bps=slip), seed+1, forecasts)
        costs.append({'name':name,'fee_bps':fee,'slippage_bps':slip,'net_mtm_pnl':result['summary']['net_mtm_pnl'],
                      'fills':result['summary']['fills'],'method':'Rerun with cost-aware quotes; fill paths may differ'})
    at='2025-01-01T00:00:00Z'
    example_basket={'base_nav':100,'currency':'USD','underlying_assets':[
        {'symbol':str(i),'weight':w,'price':p,'reference_price':100,'fx':1,'reference_fx':1,'timestamp':at}
        for i,(w,p) in enumerate([(0.25,100.4),(0.25,100.2),(0.5,100.2)])]}
    report={'created_at':created.isoformat(),'seed':seed,'event_count':count,'phase':phase,
            'mode':'paper','data_kind':'synthetic','fair_value':dislocation(fair_value(example_basket,at),100.33,.03),
            'order_flow':models,'inventory_example':hedge_decision(-5000,0,100,100_000,replace(limits,max_gross_notional=2_000_000)),
            'quote_examples':{name:dynamic_quote(100,inv,vol,5000,0,limits) for name,inv,vol in
                              [('normal',0,.0001),('high_volatility',0,.003),('long',4000,.0001),('short',-4000,.0001)]},
            'simulation':simulation,'risk':risk,'stress':stress,'validation_evidence':evidence,
            'validation':validation,'capacity':capacity,'capital':capital,'cost_sensitivity':costs,
            'limitations':['Synthetic data and stylised fills; no real ETF holdings or order book.',
                           'No historical crisis replay, real benchmark comparison, or six-month historical validation.',
                           'No actual 30-day paper observation; simulation seconds do not count as elapsed days.',
                           'Borrow availability, financing, dividends, tax and creation/redemption are not implemented.',
                           'No annualised Sharpe estimate; open positions retain mark-to-market and liquidation risk.']}
    save_json(output/'report.json',report)
    with (output/'synthetic-events.jsonl').open('w',encoding='utf-8') as stream:
        for event in events:
            stream.write(json.dumps(event,allow_nan=False)+'\n')
    for name,key in [('fair-value','fair_value'),('order-flow','order_flow'),('inventory','inventory_example'),
                     ('quotes','quote_examples'),('risk','risk'),('stress','stress'),('validation','validation'),
                     ('capital','capital'),('capacity','capacity'),('cost-sensitivity','cost_sensitivity')]:
        save_json(output/(name+'.json'),report[key])
    save_json(output/'input-example.json', example_basket)
    with (output/'fills.csv').open('w',newline='',encoding='utf-8') as stream:
        fields=list(simulation['fills'][0]) if simulation['fills'] else ['timestamp','symbol','side','signed_size']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(simulation['fills'])
    with (output/'equity.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(simulation['equity'][0]));writer.writeheader();writer.writerows(simulation['equity'])
    mapping=read_json(ROOT/'organisation.json') if (ROOT/'organisation.json').exists() else {}
    stages=[('CEO','Data Engineer','Generate timestamped synthetic inputs; reject invalid data'),
            ('Data Engineer','Quant Researcher','Market-independent fair value ready'),
            ('Quant Researcher','ML Researcher','Specify spread/liquidity/volatility targets'),
            ('ML Researcher','Trader','Validation-selected forecast delivered for held-out quotes'),
            ('Trader','Risk Manager','Proposed quotes, hedge and capital usage'),
            ('Risk Manager','Execution Agent','Bounded synthetic engineering simulation only'),
            ('Execution Agent','Validation Agent','Costed paper fills and equity ledger'),
            ('Validation Agent','Portfolio Manager','Promotion rejected; actual evidence missing'),
            ('Portfolio Manager','Reporting Agent','Zero approved strategy capital; preserve evidence')]
    with (output/'agent-messages.jsonl').open('w',encoding='utf-8') as stream:
        for seq,(sender,recipient,message) in enumerate(stages,1):
            stream.write(json.dumps({'sequence':seq,'sender':sender,'recipient':recipient,
                                    'sender_agent_id':mapping.get(sender),'recipient_agent_id':mapping.get(recipient),
                                    'message':message,'origin':'deterministic engineering run; not an LLM conversation'})+'\n')
    report_html(report,output)
    files=[ROOT/'desk.py',ROOT/'flow_models.py',ROOT/'risk_governance.py',ROOT/'run.py',FIRM/'config/risk-thresholds.json']
    save_json(output/'manifest.json',{'created_at':created.isoformat(),'seed':seed,'count':count,'epochs':epochs,
                                    'phase':phase,'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                                    'numpy_version':np.__version__, 'output_dir':str(output),
                                    'artifacts':sorted(p.name for p in output.iterdir())})
    print(json.dumps({'output':str(output),'report':str(output/'report.html'),'fills':simulation['summary']['fills'],
                      'net_mtm_pnl':simulation['summary']['net_mtm_pnl'],
                      'selected_forecast':models['selection']['model'],'strategy_approval':validation['approval'],
                      'capital_allocated':capital['allocated_capital']},indent=2))
    return output,report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=int,default=7)
    parser.add_argument('--events',type=int,default=1000)
    parser.add_argument('--epochs',type=int,default=10)
    parser.add_argument('--phase',choices=['discovery','promotion','production'],default='discovery')
    parser.add_argument('--mode',choices=['paper'],default='paper')
    args=parser.parse_args()
    run(args.seed,args.events,args.epochs,args.phase)
