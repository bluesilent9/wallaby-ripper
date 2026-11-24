# Validation — Wallaby Ripper V1

Validated on 24 November 2026. V1 (package 1.0.0) promotes the user-tested 0.1.0 implementation to stable. Only release/version metadata changed; the processing code and compatibility fixes are unchanged. All 13 application tests were rerun for V1.

## Ubuntu release matrix

Each row used the release's amd64 Ubuntu container userspace under rootless PRoot, its own system compiler/OpenSSL/libzip/curl, and the bundled source. The native tools were built, the real Adobe sample PDF was decrypted with that release's adept_remove, pdfinfo reported `Encrypted: no`, and all 13 application tests passed.

| Ubuntu userspace | Native build | Real PDF decryption | Application tests |
| --- | --- | --- | --- |
| 14.04 | PASS | PASS | 13/13 |
| 16.04 | PASS | PASS | 13/13 |
| 18.04 | PASS | PASS | 13/13 |
| 20.04 | PASS | PASS | 13/13 |
| 22.04 | PASS | PASS | 13/13 |
| 24.04 | PASS | PASS | 13/13 |
| 26.04 | PASS | PASS | 13/13 |
| 26.10 | PASS | PASS | 13/13 |

The same DEB source build is used on Kubuntu. KDE itself was not installed in these userspaces; this matrix verifies the application runtime and native dependencies, rather than a complete KDE desktop. The DEB architecture is `all`; other CPU architectures have not been tested.

PRoot was used without seccomp acceleration because of the execution environment. Its chown emulation was replaced by a no-op only inside the disposable test userspaces to permit system dependency configuration on the workspace filesystem. This workaround is not in Wallaby Ripper, the DEB, or its installer.

## Live processing and browser checks

- Adobe's public “Daisy Miller” ACSM/PDF sample from its official sample library.
- Real anonymous activation, fulfillment, decryption and pdfinfo validation on Ubuntu 14.04, 26.04 and 26.10.
- Browser selection of a fresh ticket, Start, progress stages, automatic download, and the retry download link.
- Browser downloaded “Daisy Miller.pdf”; its PDF metadata reported 80 pages and no encryption.
- Temporary encrypted PDF and uploaded ticket were absent after successful conversion.
- A ticket reused with an unrelated authorization correctly displayed the account-bound ticket error.
- Wallaby Wallet sidebar reference matched: dark green background, amber active marker, a single PDF destination.
- Desktop layout at 1366 pixels and compact layout at 390 pixels; compact DOM had no horizontal overflow.
- Original transparent wallaby-and-floppy icon installed in standard desktop icon sizes.

## DEB installation checks

The same `Architecture: all` DEB was installed with dpkg inside the 14.04 and 26.10 test userspaces after dependency resolution. Both launchers reported version 0.1.0. On each release, the installed application compiled its native tools from its packaged sources on first launch and reported backend `ready`. DEB data/control archives use gzip, and package file ownership is root:root. The final packaging revision only adds this validation document to the installed documentation.

## Automated application coverage

The tests simulate the libgourou process boundary while using the real system pdfinfo parser. They cover automatic anonymous key setup, discovery and reuse, stdin-only passwords, authorization backup/validation, cleanup after failure and success, rejecting still-encrypted output, bad XML/EPUB rejection, safe filenames, concurrency, expiration, HTTP upload/poll/download, host validation, origin validation and session-token checks. These tests do not replace the separate real native decryption checks above.

## Limitations

No claim of universal Adobe DRM compatibility: the sample verifies the ADEPT PDF path, not every book vendor, hardened DRM variant, account or license condition. Internet access and provider approval are necessary. A ticket bound to another authorization may need that original authorization. Full graphical installation tests on every Ubuntu/Kubuntu desktop and non-amd64 hardware remain unperformed.

Support-date sources are linked in README.md.
