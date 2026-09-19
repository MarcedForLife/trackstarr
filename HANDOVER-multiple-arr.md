# Multiple arr instances: remaining work

- Review **Debug → Scenarios → Multiple variants** on a physical phone,
  including selection, focus, layout and per-file versus title-wide actions.
- Add independently selectable large-series and connection-trouble demo
  scenarios, with reset behavior and browser coverage.
- Profile title loading with realistic metadata and phone/network conditions.
  Evaluate ownership indexing or caching only from those measurements; preserve
  complete groups, refresh consistency and invalidation on relevant changes.
- Verify production mounts and `MEDIA_DIRS`, download-client behavior and
  full-length UHD processing with designated test resources. Synthetic live
  acceptance does not establish these installation-specific properties.
- Complete integration review, merge and prepare a controlled deployment with
  rollback and post-deployment checks.

Future features requiring separate design: explicit arr path mapping, episode
identities for unconventional filenames, and cross-library audio extraction,
alignment and muxing. These are outside the current implementation scope.
