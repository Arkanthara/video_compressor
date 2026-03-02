## ADDED Requirements

### Requirement: NVIDIA GPU capability detection
The system SHALL detect NVIDIA GPU encoder capabilities before transcoding and determine support for H.265 NVENC and AV1 NVENC.

#### Scenario: Capability detection succeeds
- **WHEN** a transcode job is prepared on a host with supported NVIDIA hardware and drivers
- **THEN** the system records available GPU encoder capabilities for codec selection

#### Scenario: Capability detection fails
- **WHEN** the system cannot determine GPU encoder capabilities
- **THEN** the system SHALL fail planning with a diagnostic error that explains required GPU/driver prerequisites

### Requirement: Target codec selection
The system SHALL allow the target codec to be configured as `h265`, `av1`, or `auto`.

#### Scenario: Auto codec on AV1-capable hardware
- **WHEN** target codec is `auto` and AV1 NVENC support is available
- **THEN** the system SHALL select AV1 for the transcode job

#### Scenario: Auto codec on non-AV1 hardware
- **WHEN** target codec is `auto` and AV1 NVENC support is unavailable but H.265 NVENC is available
- **THEN** the system SHALL select H.265 for the transcode job

### Requirement: Deterministic fallback policy
The system SHALL support fallback modes `fail-fast`, `fallback-codec`, and `fallback-cpu` that govern behavior when the requested GPU codec path is unavailable.

#### Scenario: Fail-fast mode with unavailable codec
- **WHEN** fallback mode is `fail-fast` and the requested codec path is unsupported
- **THEN** the system SHALL terminate the job before encode start with an explicit unsupported-path error

#### Scenario: Fallback codec mode
- **WHEN** fallback mode is `fallback-codec` and requested AV1 is unsupported but H.265 is supported
- **THEN** the system SHALL continue using H.265 NVENC and report the fallback event

#### Scenario: Fallback CPU mode
- **WHEN** fallback mode is `fallback-cpu` and no supported GPU path is available
- **THEN** the system SHALL continue with configured CPU encode settings and report GPU bypass

### Requirement: Structured encoder settings
The system SHALL expose bounded encoder controls for preset and quality target and SHALL validate values before execution.

#### Scenario: Valid encoder settings
- **WHEN** a user provides supported preset and quality values
- **THEN** the system SHALL map them to valid FFmpeg encoder arguments

#### Scenario: Invalid encoder settings
- **WHEN** a user provides unsupported preset or quality values
- **THEN** the system SHALL reject the request with a validation error describing accepted values
