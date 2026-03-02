## ADDED Requirements

### Requirement: Visually lossless quality policy
The system SHALL provide a visually lossless quality mode that applies codec-specific defaults intended to preserve source visual fidelity while reducing output size.

#### Scenario: Quality mode enabled
- **WHEN** a user enables visually lossless mode for an eligible H.264 input
- **THEN** the system SHALL apply codec-specific quality defaults for the selected target codec

### Requirement: Optional objective quality validation
The system SHALL support optional post-encode quality validation using available objective metrics and configurable thresholds.

#### Scenario: Validation available and passes threshold
- **WHEN** validation is enabled and required metric tools are available and the output meets threshold
- **THEN** the system SHALL mark the output as quality-validated

#### Scenario: Validation available and fails threshold
- **WHEN** validation is enabled and the output falls below threshold
- **THEN** the system SHALL mark the output as quality-failed and report measured metric values

#### Scenario: Validation tools unavailable
- **WHEN** validation is enabled but metric tooling is unavailable in the runtime environment
- **THEN** the system SHALL report validation as unavailable without crashing the job pipeline

### Requirement: Compression outcome reporting
The system SHALL report original size, output size, compression ratio, effective codec path, and fallback events for each completed optimization job.

#### Scenario: Job completes with GPU codec path
- **WHEN** a transcode job completes using GPU acceleration
- **THEN** the system SHALL persist a result record containing size reduction and codec path metadata

#### Scenario: Job completes with fallback path
- **WHEN** a transcode job completes after any fallback behavior
- **THEN** the system SHALL include fallback reason and selected fallback path in the result record
