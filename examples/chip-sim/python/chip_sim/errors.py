class ChipSimError(Exception):
    pass


class ConfigError(ChipSimError):
    pass


class PolicyError(ChipSimError):
    pass


class SandboxError(ChipSimError):
    pass


class ReadOnlyDriveError(SandboxError):
    pass
