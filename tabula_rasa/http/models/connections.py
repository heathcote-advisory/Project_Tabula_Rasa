from typing import Literal
from pydantic import Field
from .common import DTO


class DriveFolderSetup(DTO):
    setup_id: str
    default_name: str
    picker_available: bool
    reserved: bool


class ConnectionInfo(DTO):
    provider: Literal['google_drive'] = 'google_drive'
    status: Literal['not_configured', 'pending', 'connected', 'disconnected', 'reconnect_required']
    revision: int
    self_service_available: bool = False
    authorization_status: Literal['connecting', 'failed'] | None = None
    authorized_account: str | None = None
    folder_setup: DriveFolderSetup | None = None


class ConnectionChange(DTO):
    revision: int = Field(ge=1)
    connect: bool


class ConnectionStart(DTO):
    revision: int = Field(ge=0)


class DriveSetupCommand(ConnectionStart):
    action: Literal['picker', 'create', 'cancel']
    setup_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    name: str | None = Field(default=None, max_length=255)
    parent_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,256}$')


class DriveSetupResult(DTO):
    access_token: str | None = None
    developer_key: str | None = None
    project_number: str | None = None


class ConnectionAuthorization(DTO):
    authorization_url: str
