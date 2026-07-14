# Delta for Wake & Activation

## ADDED Requirements

### Requirement: First-class Hey Aegis model install

The product SHALL document and support installing a wake model trained or tuned for “Hey Aegis”, and doctor SHALL report whether that model is present.

#### Scenario: Doctor reports missing custom model

- **GIVEN** no custom Hey Aegis model is installed
- **WHEN** the user runs `aegis doctor`
- **THEN** the output indicates wake model status and how to install or configure one

#### Scenario: Model present used by default path

- **GIVEN** a Hey Aegis model is installed at the recommended path
- **WHEN** wake engine is openWakeWord with default config
- **THEN** that model is used for KWS without requiring ad-hoc path hacking
