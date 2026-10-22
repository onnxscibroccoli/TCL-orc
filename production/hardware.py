"""Offline physical qualification evidence gate; never implements an extractor."""
import argparse
import json
import math
from pathlib import Path
try:
    from .release_gate import canonical, digest, require, read_artifact, verify_signatures
except ImportError:
    from release_gate import canonical, digest, require, read_artifact, verify_signatures

DOMAIN = 'TCL-hardware-qualification-v1'


def binomial_upper(failures, trials, confidence=.95):
    """One-sided Clopper-Pearson upper bound for independent Bernoulli trials."""
    require(type(trials) is int and trials > 0, 'positive integer trials required')
    require(type(failures) is int and 0 <= failures <= trials, 'integer failures bounds')
    require(type(confidence) in (int, float) and .95 <= confidence < 1, 'confidence bounds')
    if failures == trials:
        return 1.0
    if failures == 0:
        return -math.expm1(math.log1p(-confidence) / trials)
    target = math.log1p(-confidence)
    def logcdf(p):
        # Accumulate probabilities in log space, avoiding underflow.
        term = trials * math.log1p(-p)
        total = term
        ratio = math.log(p) - math.log1p(-p)
        for j in range(1, failures + 1):
            term += math.log(trials-j+1) - math.log(j) + ratio
            high, low = max(total, term), min(total, term)
            total = high + math.log1p(math.exp(low-high))
        return total
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if mid == lo or mid == hi:
            break
        if logcdf(mid) > target:
            lo = mid
        else:
            hi = mid
    return hi


def verify(policy, evidence, root):
    """Policy must be independently SHA256-pinned by caller; reports omit raw data."""
    try:
        require(policy.get('version') == 1, 'policy version')
        require(evidence.get('policy_sha256') == digest(canonical(policy)), 'wrong policy digest')
        require(policy.get('threshold') == 1e-6, 'required failure threshold is 1e-6')
        confidence = policy['confidence']
        require(type(confidence) in (int, float) and .95 <= confidence < 1, 'confidence bounds')
        require(type(policy.get('quorum')) is int and policy['quorum'] >= 2, 'two reviewer quorum required')
        suite = policy['extractor_suite']
        require(isinstance(suite.get('version'), str) and bool(suite['version']), 'extractor version required')
        require(evidence.get('extractor_suite') == suite, 'extractor suite mismatch')
        read_artifact(root, suite['code'])
        expected = policy['strata']
        require(isinstance(expected, list) and bool(expected), 'policy strata required')
        require({x['temperature_c'] for x in expected} == {-15,25,80}, 'required temperature corners')
        require(all(type(x['voltage_v']) in (int,float) and math.isfinite(x['voltage_v']) and x['voltage_v'] > 0 for x in expected), 'voltage conditions required')
        bounds = policy['voltage_bounds_v']
        require(len(bounds) == 2 and all(type(v) in (int,float) and math.isfinite(v) for v in bounds) and 0 < bounds[0] < bounds[1], 'distinct finite voltage bounds required')
        require(len(expected) == 6, 'six distinct qualification corners required')
        require(all({x['voltage_v'] for x in expected if x['temperature_c'] == t} == set(bounds) for t in (-15,25,80)), 'every temperature must cover voltage bounds')
        labels = lambda rows: [(x['temperature_c'], x['voltage_v']) for x in rows]
        require(len(set(labels(expected))) == len(expected), 'duplicate policy strata')
        rows = evidence['strata']
        require(len(rows) == len(expected) and set(labels(rows)) == set(labels(expected)), 'missing or extra qualification strata')
        require(evidence.get('purpose') == 'physical-enrollment-qualification', 'physical evidence purpose required')
        for name in ('experiment_metadata', 'independence_protocol', 'helper_authentication_tests'):
            read_artifact(root, evidence[name])
        declaration = evidence['independence_declaration']
        for name in ('device_population', 'sample_selection', 'independent_trial_definition', 'correlation_handling'):
            require(isinstance(declaration.get(name), str) and declaration[name].strip(), 'independence declaration incomplete')
        # Reviewers approve exact evidence, including the independent-trial interpretation
        # and helper-data authentication test artifact, not an unaudited Boolean.
        payload = {'domain': DOMAIN, 'evidence': {k:v for k,v in evidence.items() if k != 'approvals'}}
        verify_signatures(canonical(payload), evidence.get('approvals', []), policy['review_keys'], policy['quorum'])
        results = []
        for row in rows:
            read_artifact(root, row['enrolled_measurements'])
            require(type(row.get('device_count')) is int and row['device_count'] > 0, 'device count required')
            upper = binomial_upper(row['failures'], row['independent_trials'], confidence)
            require(upper < policy['threshold'], 'insufficient independent trials or excessive failures')
            results.append({'temperature_c':row['temperature_c'], 'voltage_v':row['voltage_v'], 'upper_failure_probability':upper, 'independent_trials':row['independent_trials'], 'failures':row['failures']})
        return {'status':'conditional-acceptance', 'policy_sha256':digest(canonical(policy)), 'confidence_per_stratum':confidence, 'strata':results, 'condition':'Artifact authenticity, physical provenance and independence rely on signed reviewer assessment; no extractor security or physical performance is established by this program alone.'}
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as error:
        return {'status':'blocked', 'reason':str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('policy','evidence','root','policy-sha256'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    try:
        policy = json.loads(Path(args.policy).read_text())
        require(digest(canonical(policy)) == args.policy_sha256, 'external policy pin mismatch')
        report = verify(policy, json.loads(Path(args.evidence).read_text()), args.root)
    except (ValueError, OSError) as error:
        report = {'status':'blocked', 'reason':str(error)}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'conditional-acceptance' else 1

if __name__ == '__main__':
    raise SystemExit(main())
