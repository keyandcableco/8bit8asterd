"""pcbnew.SaveBoard() rewrites 8b8_v2.kicad_pro and drops schematic-side
settings.  Put back the one ERC decision this design makes."""
import json
import os

PRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '8b8_v2.kicad_pro')


def restore_erc():
    with open(PRO) as f:
        d = json.load(f)
    # The three YM2149 analog outputs are summed on purpose (as on v1.0), which
    # ERC reports as output-to-output.  Keep it visible as a warning, not an error.
    d.setdefault('erc', {}).setdefault('rule_severities', {})['pin_to_pin'] = 'warning'
    with open(PRO, 'w') as f:
        json.dump(d, f, indent=2)
        f.write('\n')
