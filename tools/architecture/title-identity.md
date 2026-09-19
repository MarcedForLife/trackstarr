# One title across instances

A film or series shared by several arr instances appears as one poster, one
sheet and one file list. Files retain their source and independent work state.

## Identity and ownership

`Title` contains an ordered tuple of `Source` records. Each source carries its
folder, arr instance, item ID and folder ownership metadata. A title requires at
least one source; its primary folder, arr, item ID and slug are derived properties. Catalogue refresh
resolves folder ownership first, then merges titles by kind and provider ID
(TMDB for films, TVDB for series), falling back to IMDb ID. Unclaimed folders
remain standalone; names and years are not fuzzy-matched.

The first configured source is primary: defaults first, then named instances
alphabetically. It supplies the title ID, name, poster and other title metadata.
The ID remains `arr:<instance>:<item>` or `dir:<folder>`. Current member IDs are
aliases in the shared title index used by shelf reads and local pause actions. Every source folder maps to the merged title, so
rollups and file ownership cover all variants. Shared-folder conflicts retain
first-configured ownership.

The catalogue retains each unchanged connection's last successful titles during
an outage and marks the view incomplete. This preserves merged identity and
folder ownership until recovery. A healthy empty response, connection removal
or changed credentials/address discards the affected cached source. Local pause
actions resolve aliases without waiting for network or verdict-cache reads;
duplicate aliases target each folder once, and unknown IDs refuse the batch.

## Detail and actions

Title detail includes every source folder and labels each claimed file with its
source. Episode grouping and cumulative pagination keep a group's variants
together. A card reports a variant count when multiple sources hold the title.

Title-wide hold, resume, skip and re-check controls cover all source folders;
selecting a file does not narrow their scope. File actions remain path-specific.
Holds are enforced by path. Once detail loads, the UI matches title holds to
current source folders rather than their persisted title labels, so a new
primary ID does not hide Resume. Resuming affects current folders; a hold on a
removed source remains in place and becomes visible if that source returns.
Links include each arr source; media-server lookups and cover retrieval can try
multiple source folders. Imports, jobs and rescans retain their originating
instance identity.

The UI uses one reading state per opening. Variant selectors distinguish source,
quality, size and status, with path details available for ambiguity. The
**Multiple variants** demo adds a second source and 4K files to existing titles.

## Boundaries

Distinct variants require distinct paths visible to Trackstarr. Arr path
remapping, fuzzy title matching and cross-instance audio transfer are not part
of this model.
