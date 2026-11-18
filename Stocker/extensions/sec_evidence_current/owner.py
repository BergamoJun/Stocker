"""Owner pins are operator inputs, never claims read from request/report JSON."""
from dataclasses import dataclass
from pathlib import Path
from copy import deepcopy
from research.sec_transport import now
from research.sec_report import ROOT
from research.rights import as_utc
from research.validation import digest,stable_hash
from extensions.sec_evidence_v1_1 import reviews
from . import read,verify_seal,Rejected


@dataclass(frozen=True)
class OwnerContext:
    illustrative: bool
    policy_path: object = None
    policy_sha256: object = None
    receipt_path: object = None
    receipt_sha256: object = None

    @classmethod
    def load(cls,path,pinned_sha256,*,illustrative=False,registry=None,clock=now):
        if not illustrative and clock is not now:raise Rejected('REAL_TRUST_CLOCK_OVERRIDE_FORBIDDEN')
        if not path or not pinned_sha256:raise Rejected('EXTERNAL_OWNER_PIN_REQUIRED')
        root=Path(registry or ROOT/'reports/private/sec_current_owner_registry'/('illustrative' if illustrative else 'real'))
        if not illustrative and not root.resolve().is_relative_to((ROOT/'reports/private/sec_current_owner_registry').resolve()):
            raise Rejected('OWNED_CURRENT_TRUST_REGISTRY_REQUIRED')
        trust=reviews.load_trust(path,pinned_sha256,illustrative=illustrative,registry=root,clock=clock)
        receipt=root/(pinned_sha256+'.json')
        if not (trust['trusted_review_hashes'] or trust['trusted_event_review_hashes']):
            return cls(illustrative,Path(path),pinned_sha256)
        return cls(illustrative,Path(path),pinned_sha256,receipt,digest(receipt))

    def trust(self,illustrative):
        if self.illustrative!=illustrative:raise Rejected('OWNER_NAMESPACE_MISMATCH')
        if self.policy_path is None:
            if any(v is not None for v in [self.policy_sha256,self.receipt_path,self.receipt_sha256]):raise Rejected('OWNER_CONTEXT_INVALID')
            return reviews.empty_trust(illustrative)
        if digest(self.policy_path)!=self.policy_sha256:raise Rejected('OWNER_POLICY_PIN_CHANGED')
        value=read(self.policy_path)
        # Reuse the original closed trust schema and first-seen policy, without
        # ever using the old user's SEC registry or allowing a report to choose it.
        from extensions.sec_evidence_v1_1.contract import original
        original.validate(value,'trust')
        if value['is_illustrative']!=illustrative:raise Rejected('OWNER_NAMESPACE_MISMATCH')
        if value['trusted_review_hashes'] or value['trusted_event_review_hashes']:
            if not self.receipt_path or not self.receipt_sha256 or digest(self.receipt_path)!=self.receipt_sha256:
                raise Rejected('OWNER_FIRST_AVAILABILITY_CHANGED')
            receipt=read(self.receipt_path);verify_seal(receipt)
            if receipt['owner_policy_sha256']!=self.policy_sha256 or receipt['policy']!=value:
                raise Rejected('OWNER_FIRST_AVAILABILITY_CHANGED')
            value={**value,'available_at':max([value['available_at'] or receipt['first_available_at'],receipt['first_available_at']],key=as_utc)}
        return deepcopy(value)

    def binding(self,illustrative):
        trust=self.trust(illustrative)
        return {'policy_sha256':self.policy_sha256,'first_seen_receipt_sha256':self.receipt_sha256,
                'trust_sha256':stable_hash(trust),'is_illustrative':illustrative}


def context(owner,illustrative):
    if owner is None:return OwnerContext(illustrative)
    if not isinstance(owner,OwnerContext):raise Rejected('EXTERNAL_OWNER_CONTEXT_REQUIRED')
    owner.trust(illustrative)
    return owner
