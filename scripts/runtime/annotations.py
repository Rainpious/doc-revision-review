"""Apply explicit upstream review annotations; never infer professional content."""
import argparse
import copy
from revision_tool import load, save, seal, validate, ContractError


def apply(data, annotations):
    validate(data)
    if data['package_id'] != annotations['package_id'] or data['working_copy']['state_hash'] != annotations['base_hash']:
        raise ContractError('annotation package/baseline mismatch')
    if data['operations']:
        raise ContractError('upstream annotation requires an unedited source package')
    allowed={'reasons','changes','opinions','decisions','summary'}
    if set(annotations['replace']) - allowed:
        raise ContractError('annotation field outside allowed scope')
    out=copy.deepcopy(data)
    out.update(copy.deepcopy(annotations['replace']))
    return validate(seal(out))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('annotations');p.add_argument('output')
    a=p.parse_args();save(a.output,apply(load(a.input),load(a.annotations)));print('OK annotations')
