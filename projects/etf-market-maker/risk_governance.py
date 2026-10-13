"""Deterministic paper-desk risk calculations and fail-closed governance.

Evidence fields are assertions from documented records; this module does not
authenticate those records or confer authority to deploy actual capital.
"""
import math
import numpy as np


def _number(value, minimum=None, maximum=None):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (minimum is None or value >= minimum)
            and (maximum is None or value <= maximum))


def risk_report(exposures, return_scenarios):
    """Dollar positions (ETF and hedge) and rows of aligned simple returns.

    Each row is ONE caller-defined scenario period, not automatically a day.
    Returns var/es dollars keyed '95', '99', '99.9'. Historical empirical
    nearest-rank quantiles avoid distribution assumptions; tail ES uses losses
    at/above that quantile, including ties. No normal scaling or forecast claim.
    """
    try:
        positions = np.asarray(exposures, dtype=float)
        returns = np.asarray(return_scenarios, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError('Numeric exposures and return scenarios required') from exc
    if (positions.ndim != 1 or positions.size == 0 or returns.ndim != 2
            or returns.shape[1] != positions.size or returns.shape[0] < 2
            or not np.isfinite(positions).all() or not np.isfinite(returns).all()):
        raise ValueError('Finite aligned exposures and at least two scenario rows required')
    losses = -(returns @ positions)
    if not np.isfinite(losses).all():
        raise ValueError('Scenario loss overflow')
    result = dict(var={}, es={}, tail_uncertainty={}, sample_count=len(losses),
                  horizon='one input scenario period; caller must document duration',
                  method='Empirical nearest-rank loss quantile; ES averages losses >= quantile')
    for label, confidence in [('95', .95), ('99', .99), ('99.9', .999)]:
        ordered = np.sort(losses)
        quantile = float(ordered[math.ceil(confidence * len(losses)) - 1])
        tail = losses[losses >= quantile]
        result['var'][label] = max(0., quantile)
        result['es'][label] = max(0., float(tail.mean()))
        result['tail_uncertainty'][label] = dict(
            observed_tail_count=len(tail), expected_tail_count=len(losses) * (1-confidence),
            insufficient_tail_sample=len(losses) * (1-confidence) < 20,
            note='Fewer than 20 expected tail observations is unstable; ties do not add tail resolution.')
    return result


def stress_report(etf_inventory, hedge_units, etf_price, hedge_price, spread, liquidity):
    """Signed units, positive prices, absolute full spread and depth in units.

    Named scenarios are illustrative event-INSPIRED shocks, NOT historical
    replays. The hedge is a basket proxy. Forced liquidation covers BOTH legs,
    charging stressed half-spread and linear depth impact per leg; spread/depth
    assumptions apply equally to both legs. No guarantee of executable fills.
    """
    values = [etf_inventory, hedge_units, etf_price, hedge_price, spread, liquidity]
    if not all(_number(v) for v in values) or min(etf_price, hedge_price, liquidity) <= 0 or spread < 0:
        raise ValueError('Finite positions, positive prices/depth and nonnegative spread required')
    scenarios = []
    for name, shock, basis, widen, depth in [
            ('COVID_inspired', .30, .03, 4., .30),
            ('2008_inspired', .45, .05, 5., .20),
            ('flash_crash_inspired', .12, .06, 8., .10),
            ('Volmageddon_inspired', .10, .04, 6., .25),
            ('liquidity_shock', .03, .02, 3., .50)]:
        for direction in [-1, 1]:
            etf_return = direction * (shock + basis)
            hedge_return = direction * shock
            pnl = etf_inventory * etf_price * etf_return + hedge_units * hedge_price * hedge_return
            stressed_spread = spread * widen
            stressed_depth = liquidity * depth
            cost = sum(abs(units) * stressed_spread / 2 * (1 + abs(units) / stressed_depth)
                       for units in [etf_inventory, hedge_units])
            scenarios.append(dict(name=name + ('_down' if direction < 0 else '_up'),
                                  etf_return=etf_return, hedge_return=hedge_return,
                                  basis_divergence=etf_return-hedge_return,
                                  spread_multiplier=widen, liquidity_multiplier=depth,
                                  mark_to_market_pnl=pnl, liquidation_cost=cost, net_pnl=pnl-cost))
    return dict(scenarios=scenarios,
                methodology='Hypothetical event-inspired scenarios, NOT historical replay; both directions; full forced liquidation',
                limitation='Linear depth-impact toy model; basket tracking, borrow and market closure risk are not fully modelled')


def validate_strategy(evidence, thresholds):
    """Fail closed for promotion beyond engineering; never live approval.

    thresholds: {min_sharpe: finite >=0, max_drawdown: fraction in [0,1]}.
    evidence: data_kind='historical'; historical_months>=6; paper_days>=30;
    oos_documented/costs_documented/actual_paper_days_documented/
    independent_backtest/independent_risk_review must be bool True.
    sharpe, max_drawdown, net_pnl_after_costs; numeric feature_timestamps and
    decision_timestamps aligned and nonempty; regimes includes bull/bear/sideways;
    capacity_gbp>=100m; positive proposed_daily_volume_gbp, market_daily_volume_gbp,
    max_participation<=1, fx_gbp_per_quote; nonempty fx_assumption, benchmark,
    sharpe_convention. Timestamps represent availability (including publication
    delays), not the period an observation describes. Capacity target is capital;
    proposed volume must cover the evaluated strategy's turnover at that capital.
    """
    reasons = []
    if not isinstance(evidence, dict) or not isinstance(thresholds, dict):
        return dict(approval=False, reasons=['Evidence and thresholds must be dictionaries'], eligibility='engineering_only')
    if not _number(thresholds.get('min_sharpe'), 0) or not _number(thresholds.get('max_drawdown'), 0, 1):
        reasons.append('Missing or malformed owner thresholds')
    if evidence.get('data_kind') != 'historical':
        reasons.append('Synthetic or unidentified data supports engineering only')
    for field in ['oos_documented', 'costs_documented', 'actual_paper_days_documented',
                  'independent_backtest', 'independent_risk_review']:
        if evidence.get(field) is not True:
            reasons.append(field + ' must be documented and true')
    for field, low, high in [('historical_months', 6, None), ('paper_days', 30, None),
                             ('sharpe', None, None), ('max_drawdown', 0, 1),
                             ('net_pnl_after_costs', None, None), ('capacity_gbp', 100_000_000, None),
                             ('proposed_daily_volume_gbp', 0, None), ('market_daily_volume_gbp', 0, None),
                             ('max_participation', 0, 1), ('fx_gbp_per_quote', 0, None)]:
        if not _number(evidence.get(field), low, high):
            reasons.append(field + ' missing, malformed or below requirement')
    for field in ['net_pnl_after_costs', 'proposed_daily_volume_gbp', 'market_daily_volume_gbp',
                  'max_participation', 'fx_gbp_per_quote']:
        if _number(evidence.get(field)) and evidence[field] <= 0:
            reasons.append(field + ' must be positive')
    for field in ['fx_assumption', 'benchmark', 'sharpe_convention']:
        if not isinstance(evidence.get(field), str) or not evidence[field].strip():
            reasons.append(field + ' must be documented')
    for field, threshold, exceeds in [('sharpe', 'min_sharpe', False), ('max_drawdown', 'max_drawdown', True)]:
        a, b = evidence.get(field), thresholds.get(threshold)
        if _number(a) and _number(b) and ((a > b) if exceeds else (a < b)):
            reasons.append(field + ' fails phase threshold')
    features, decisions = evidence.get('feature_timestamps'), evidence.get('decision_timestamps')
    if (not isinstance(features, list) or not isinstance(decisions, list) or not features
            or len(features) != len(decisions)
            or not all(_number(x) for x in features + decisions)):
        reasons.append('Missing or malformed feature availability/decision timestamps')
    elif any(a > b for a, b in zip(features, decisions)):
        reasons.append('Data leakage: feature became available after decision')
    regimes = evidence.get('regimes')
    if not isinstance(regimes, list) or not all(isinstance(x, str) for x in regimes) or not {'bull', 'bear', 'sideways'}.issubset(regimes):
        reasons.append('Regime coverage must include bull, bear and sideways')
    volume, adv, cap = (evidence.get(k) for k in ['proposed_daily_volume_gbp', 'market_daily_volume_gbp', 'max_participation'])
    if all(_number(x, 0) for x in [volume, adv, cap]) and volume > adv * cap:
        reasons.append('GBP100m capacity fails daily participation limit')
    return dict(approval=not reasons, reasons=reasons,
                eligibility='further_paper_review' if not reasons else 'engineering_only',
                limitation='Structured assertions require independent verification of actual dated evidence; no live authority')


def allocate_capital(proposals, total_capital):
    """Allocate PAPER capital proportionally to approved requests/caps.

    proposals: [{name, requested_capital, votes:{quant:'buy',
    fundamental:'buy'|'neutral', risk:'approve'|'reduce_size', execution:'buy',
    validation:'approved'}, risk_cap: amount required for reduce_size}].
    Any absent/malformed/unknown vote fails closed. Risk veto and validation
    rejection cannot be overridden. Caps and budget are in the SAME currency.
    """
    if not isinstance(proposals, list) or not _number(total_capital, 0):
        raise ValueError('Proposals list and finite nonnegative capital required')
    rows = []
    allowed = dict(quant={'buy'}, fundamental={'buy', 'neutral'}, risk={'approve', 'reduce_size'},
                   execution={'buy'}, validation={'approved'})
    for proposal in proposals:
        if not isinstance(proposal, dict):
            proposal = {}
        votes = proposal.get('votes', {})
        reasons = []
        if not isinstance(votes, dict):
            votes = {}
        for role, choices in allowed.items():
            if not isinstance(votes.get(role), str) or votes[role] not in choices:
                reasons.append(role + ' vote missing or not approving')
        requested = proposal.get('requested_capital')
        if not _number(requested, 0):
            reasons.append('Malformed capital request')
        ceiling = requested if _number(requested, 0) else 0
        if votes.get('risk') == 'reduce_size':
            if not _number(proposal.get('risk_cap'), 0):
                reasons.append('Risk reduction requires explicit nonnegative risk_cap')
            else:
                ceiling = min(ceiling, proposal['risk_cap'])
        rows.append(dict(name=proposal.get('name', 'unnamed'), votes=votes,
                         eligible_capital=0 if reasons else ceiling, reasons=reasons))
    eligible = sum(row['eligible_capital'] for row in rows)
    scale = min(1., total_capital / eligible) if eligible else 0
    remaining = total_capital
    for row in rows:
        row['allocated_capital'] = min(remaining, row['eligible_capital'] * scale)
        remaining -= row['allocated_capital']
    return dict(allocations=rows, allocated_capital=total_capital-remaining,
                unallocated_capital=remaining, paper_only=True,
                policy='Unanimous required approvals; risk and validation veto; proportional capped allocation')
