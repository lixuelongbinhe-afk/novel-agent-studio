# Windows code-signing policy

Novel Agent Studio distinguishes local development packages from official release packages.

## Local development

`scripts/package-desktop.ps1` may produce unsigned artifacts when no signing environment is configured. These artifacts are for local testing and must not be described as trusted official builds.

## Official release

The release operator provides an Authenticode code-signing certificate through CI secrets and sets:

- `NAS_SIGN_CERTIFICATE_PATH`: temporary path to the CI-provisioned PFX file.
- `NAS_SIGN_CERTIFICATE_PASSWORD`: PFX password, supplied only as a masked secret.
- `NAS_SIGN_TIMESTAMP_URL`: RFC 3161 timestamp service; defaults to DigiCert.
- `NAS_REQUIRE_CODE_SIGNING=1`: fail closed when the certificate is unavailable.

The packaging script signs and verifies `NovelAgentStudio.exe`, `NovelAgentStudioConsole.exe`, `Uninstall.exe`, and the final installer. The portable ZIP contains the three signed application executables. SHA-256 checksums and the dependency SBOM are published beside both packages.

The certificate file must be created in the runner temporary directory, deleted after packaging, and never committed. Release maintainers verify the Git tag, generated provenance manifest, Authenticode signature, timestamp, checksum, and SBOM before publishing.
