# One title across instances

A film or series shared by several arr instances appears as one poster, one
sheet and one file list. Files retain their source and independent work state.

## Identity and ownership

`Title` contains an ordered tuple of `Source` records. Each source carries its
folder, arr instance, item ID and folder ownership metadata. Catalogue refresh
resolves folder ownership first, then merges titles by kind and provider ID
(TMDB for films, TVDB for series), falling back to IMDb ID. Unclaimed folders
remain standalone; names and years are not fuzzy-matched.

The first configured source is primary: defaults first, then named instances
alphabetically. It supplies the title ID, name, poster and other title metadata.
The ID remains `arr:<instance>:<item>` or `dir:<folder>`. Current member IDs are
aliases in the shelf index. Every source folder maps to the merged title, so
rollups and file ownership cover all variants. Shared-folder conflicts retain
first-configured ownership.

## Detail and actions

Title detail includes every source folder and labels each claimed file with its
source. Episode grouping and cumulative pagination keep a group's variants
together. A card reports a variant count when multiple sources hold the title.

Title-wide hold, resume, skip and re-check controls cover all source folders;
selecting a file does not narrow their scope. File actions remain path-specific.
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
