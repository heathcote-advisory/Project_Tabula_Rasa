"""Reference-data workflows using neutral ports and shared domain rules."""

from collections.abc import Callable, Sequence
from typing import Unpack, cast

from tabula_rasa.errors import StorageValidationError
from tabula_rasa.quickbooks_import import suggested_account_classification
from tabula_rasa.reference_data import (
    ACCOUNT_TYPES,
    as_bool,
    balance_amount,
    clean_text,
    normalize_merge_ids,
    normalize_payee_name,
    normalize_tag_name,
    validate_account_classification,
    vehicle_name,
)

from .contracts import (
    AccountChanges,
    AccountRecord,
    AccountRepository,
    CategoryChanges,
    CategoryRecord,
    CategoryRepository,
    ExpectedRecord,
    ExpectedRecords,
    ManagedMutationResult,
    ManagedReferenceChanges,
    ManagedReferenceRecord,
    PayeeRepository,
    TagRepository,
)


def _account_type(value: str) -> str:
    account_type = clean_text(value, "Account type", required=True)
    if account_type not in ACCOUNT_TYPES:
        raise StorageValidationError("Unknown account type")
    return account_type


def _account_changes(changes: AccountChanges) -> AccountChanges:
    normalized = dict(changes)
    if set(changes) - set(AccountChanges.__optional_keys__):
        raise StorageValidationError("Unknown account update field")
    if "name" in changes:
        normalized["name"] = clean_text(changes["name"], "Account name", required=True)
    if "type" in changes:
        normalized["type"] = _account_type(changes["type"])
    if "classification" in changes:
        classification = clean_text(
            changes["classification"], "Account classification", required=True
        ).title()
        # A partial update has no current record. The repository validates the
        # final pair against fresh data, without an extra service read.
        validate_account_classification(normalized.get("type", "Other"), classification)
        normalized["classification"] = classification
    if "opening_balance" in changes:
        normalized["opening_balance"] = balance_amount(changes["opening_balance"])
    if "active" in changes:
        normalized["active"] = as_bool(changes["active"])
    if "parent_id" in changes:
        normalized["parent_id"] = clean_text(changes["parent_id"], "Account parent")
    return cast(AccountChanges, normalized)


def _managed_name(value: str, label: str, normalize: Callable[[object], str]) -> str:
    name = clean_text(value, f"{label} name", required=True)
    if not normalize(name):
        raise StorageValidationError(f"{label} name is required")
    return name


def _managed_changes(
    changes: ManagedReferenceChanges,
    label: str,
    normalize: Callable[[object], str],
) -> ManagedReferenceChanges:
    normalized = dict(changes)
    if set(changes) - set(ManagedReferenceChanges.__optional_keys__):
        raise StorageValidationError(f"Unknown {label.lower()} update field")
    if "name" in changes:
        normalized["name"] = _managed_name(changes["name"], label, normalize)
    if "active" in changes:
        normalized["active"] = as_bool(changes["active"], f"{label} active")
    return cast(ManagedReferenceChanges, normalized)


class AccountService:
    """Normalize account inputs; delegate final fresh invariants to the port."""

    def __init__(self, repository: AccountRepository) -> None:
        self._repository = repository

    def list(self, *, active_only: bool = True) -> list[AccountRecord]:
        return self._repository.list(active_only=active_only)

    def create(
        self,
        name: str,
        account_type: str,
        opening_balance: object = 0,
        *,
        classification: str | None = None,
        parent_id: str | None = None,
    ) -> AccountRecord:
        name = clean_text(name, "Account name", required=True)
        account_type = _account_type(account_type)
        classification = clean_text(
            classification
            if classification is not None
            else suggested_account_classification(name, account_type),
            "Account classification",
            required=True,
        ).title()
        validate_account_classification(account_type, classification)
        return self._repository.create(
            name,
            account_type,
            balance_amount(opening_balance),
            classification=classification,
            parent_id=clean_text(parent_id, "Account parent"),
        )

    def update(
        self, account_id: str, *, expected: ExpectedRecord | None = None,
        **changes: Unpack[AccountChanges]
    ) -> AccountRecord:
        account_id = clean_text(account_id, "Account ID", required=True)
        return self._repository.update(
            account_id, changes=_account_changes(changes),
            **({"expected": expected} if expected is not None else {}),
        )

    def rename(
        self, account_id: str, name: str, *, expected: ExpectedRecord | None = None,
    ) -> AccountRecord:
        return self.update(account_id, name=name, expected=expected)

    def deactivate(
        self, account_id: str, *, expected: ExpectedRecord | None = None,
    ) -> AccountRecord:
        return self.update(account_id, active=False, expected=expected)


class CategoryService:
    """Normalize categories without repeating fresh persistence invariants."""

    def __init__(self, repository: CategoryRepository) -> None:
        self._repository = repository

    @staticmethod
    def _kind(value: str) -> str:
        kind = clean_text(value, "Category kind", required=True).title()
        if kind not in {"Income", "Expense"}:
            raise StorageValidationError("Category kind must be Income or Expense")
        return kind

    def list(self, kind: str | None = None) -> list[CategoryRecord]:
        return self._repository.list(None if kind is None else self._kind(kind))

    def create(
        self, name: str, kind: str, *, parent_id: str | None = None
    ) -> CategoryRecord:
        return self._repository.create(
            clean_text(name, "Category name", required=True),
            self._kind(kind),
            parent_id=clean_text(parent_id, "Category parent"),
        )

    def update(
        self, category_id: str, *, expected: ExpectedRecord | None = None,
        **changes: Unpack[CategoryChanges]
    ) -> CategoryRecord:
        normalized = dict(changes)
        if "name" in changes:
            normalized["name"] = clean_text(
                changes["name"], "Category name", required=True
            )
        if "kind" in changes:
            normalized["kind"] = self._kind(changes["kind"])
        if "parent_id" in changes:
            normalized["parent_id"] = clean_text(changes["parent_id"], "Category parent")
        return self._repository.update(
            clean_text(category_id, "Category ID", required=True),
            changes=cast(CategoryChanges, normalized),
            **({"expected": expected} if expected is not None else {}),
        )

    def delete(self, category_id: str, *, expected: ExpectedRecord | None = None) -> None:
        self._repository.delete(
            clean_text(category_id, "Category ID", required=True),
            **({"expected": expected} if expected is not None else {}),
        )


class PayeeService:
    """Manage payee identity through one profile's supplied repository."""

    def __init__(self, repository: PayeeRepository) -> None:
        self._repository = repository

    def list(self, *, active_only: bool = False) -> list[ManagedReferenceRecord]:
        return self._repository.list(active_only=active_only)

    def create(self, name: str, *, active: bool = True) -> ManagedReferenceRecord:
        return self._repository.create(
            _managed_name(name, "Payee", normalize_payee_name),
            active=as_bool(active, "Payee active"),
        )

    def update(
        self,
        payee_id: str,
        *,
        expected: ExpectedRecord | None = None,
        **changes: Unpack[ManagedReferenceChanges],
    ) -> ManagedReferenceRecord:
        payee_id = clean_text(payee_id, "Payee", required=True)
        return self._repository.update(
            payee_id,
            changes=_managed_changes(changes, "Payee", normalize_payee_name),
            expected=expected,
        )

    def rename(
        self, payee_id: str, name: str, *, expected: ExpectedRecord | None = None
    ) -> ManagedReferenceRecord:
        return self.update(payee_id, name=name, expected=expected)

    def merge(
        self,
        target_id: str,
        source_ids: Sequence[str],
        *,
        expected: ExpectedRecords | None = None,
    ) -> ManagedMutationResult:
        target_id, source_ids = normalize_merge_ids(target_id, source_ids, "payee")
        return self._repository.merge(target_id, source_ids, expected=expected)

    def remove(
        self, payee_id: str, *, expected: ExpectedRecord | None = None
    ) -> ManagedMutationResult:
        payee_id = clean_text(payee_id, "Payee", required=True)
        return self._repository.remove(payee_id, expected=expected)


class ManagedReferenceService:
    """Manage named references through one profile's supplied repository."""

    def __init__(self, repository: TagRepository, *, label="Tag", normalize=normalize_tag_name) -> None:
        self._repository = repository
        self.label, self.normalize = label, normalize

    def list(self, *, active_only: bool = False) -> list[ManagedReferenceRecord]:
        return self._repository.list(active_only=active_only)

    def _name(self, value):
        return _managed_name(value, self.label, self.normalize)

    def create(self, name: str, *, active: bool = True) -> ManagedReferenceRecord:
        return self._repository.create(
            self._name(name),
            active=as_bool(active, self.label + " active"),
        )

    def update(
        self,
        tag_id: str,
        *,
        expected: ExpectedRecord | None = None,
        **changes: Unpack[ManagedReferenceChanges],
    ) -> ManagedReferenceRecord:
        tag_id = clean_text(tag_id, self.label, required=True)
        normalized = _managed_changes(changes, self.label, self.normalize)
        if 'name' in changes:
            normalized['name'] = self._name(changes['name'])
        return self._repository.update(
            tag_id,
            changes=normalized,
            expected=expected,
        )

    def rename(
        self, tag_id: str, name: str, *, expected: ExpectedRecord | None = None
    ) -> ManagedReferenceRecord:
        return self.update(tag_id, name=name, expected=expected)

    def remove(
        self, tag_id: str, *, expected: ExpectedRecord | None = None
    ) -> ManagedMutationResult:
        tag_id = clean_text(tag_id, self.label, required=True)
        return self._repository.remove(tag_id, expected=expected)


class TagService(ManagedReferenceService):
    """Manage tags using the shared name/active workflow."""

    def merge(
        self,
        target_id: str,
        source_ids: Sequence[str],
        *,
        expected: ExpectedRecords | None = None,
    ) -> ManagedMutationResult:
        target_id, source_ids = normalize_merge_ids(target_id, source_ids, "tag")
        return self._repository.merge(target_id, source_ids, expected=expected)



class VehicleService(ManagedReferenceService):
    """Manage exact vehicle labels without merging historical spelling variants."""
    def __init__(self, repository):
        super().__init__(repository, label='Vehicle', normalize=str)

    def _name(self, value):
        return vehicle_name(value)
