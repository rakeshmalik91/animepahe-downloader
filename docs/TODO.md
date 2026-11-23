## 2026.10.25
- [x] Wrong year fetched for overgeared - to check why
> Root Cause:
> - AnimePahe fuzzy search for "Overgeared" returned both "Overgeared" (2026, Currently Airing) and "Animegataris" (2017, Finished Airing).
> - Previous logic treated search results as franchise seasons without filtering, taking min(years)=2017 with ongoing flag -> (2017-).
> Solution Implemented:
> - Single season folders only fetch year for the specific anime (e.g. Overgeared -> 2026).
> - Multi-season folders filter search results using strict franchise matching before calculating oldest-latest span.
> - Full hierarchy implemented: manual `-y/--year` > AnimePahe metadata > Jikan (MAL) > AnimePahe official title string.
> - Preserves existing folder years unless `--update-years` is passed.
> - `--add-years` defaults to True, `--update-years` defaults to False, `--year-tags` removed.
> Expected logic:
> - if user has specified year manually with -—year or -y param, use it
> - if anime folder contains multiple seasons, find all seasons from animepahe and fetch the oldest and latest year from animepahe. If latest year has finished airing format should be like (2017-2026) else (2017-)
> - if it is just a single season folder, fetch year from animepahe
> - if no year found in animepahe, check jikan, mal etc.
> - if still no year found but anime title from animepahe (not user provided title or folder name) has an year use it. e.g. “Ranma ½ (2024)”
> - if folder name already contains a year, dont change it (unless —-update-years is passed)
> > - —-add-years adds years to folders that doesnt have year, defaults true
> > - —-update-years adds years to folders that doesnt have year, and update correct years to folders that already have year as well, defaults false
> > - —-year or -y to manually specify year, overrides both above params. only works for single anime download, not complete library scan.
> > - To remove —-year-tags, its redundant
> > > - my-idm needs to pass —-add-years or —-update-years based on config
> > > - existing older downloads for same anime should be in new folder when —update-years is used, and my-idm should show file not found for them, no need to handle it

### Older
- [x] dont trigger MyIDM launch, if no downloads are required
- [ ] windows taskbar doesnt show the icon when run from pyw, instead it show a generic python program icon