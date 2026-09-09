"""Finding a title inside Plex, Jellyfin and the *arrs, against faked HTTP.
No network."""

import urllib.error

import pytest

from conftest import set_config
from trackstarr import links, media_server

PLEX_SECTIONS = {
    "MediaContainer": {
        "Directory": [
            {"key": "1", "Location": [{"path": "/data/media/movies"}]},
            {"key": "2", "Location": [{"path": "/data/media/tv"}]},
        ]
    }
}

IDENTITY = {"MediaContainer": {"machineIdentifier": "abc123"}}


def plex_movie(rating_key, title, year, file):
    return {
        "ratingKey": rating_key,
        "title": title,
        "year": year,
        "Media": [{"Part": [{"file": file}]}],
    }


def plex_show(rating_key, title, year):
    return {"ratingKey": rating_key, "title": title, "year": year}


def container(*entries):
    return {"MediaContainer": {"Metadata": list(entries)}}


def find(folder, name, year=None, **known):
    """The links for one title, spelled the way a caller holds it: the folder
    and the name, plus the *arr and slug when an *arr claims it."""
    return links.for_title(links.Subject(folder, name, year, **known))


@pytest.fixture(autouse=True)
def _fresh_state():
    """The module caches outlive a request on purpose, so every test clears
    them; one leaking would be a link resolved from another test's server."""
    links.forget()
    media_server.reset()
    yield
    links.forget()
    media_server.reset()


@pytest.fixture
def plex():
    set_config(PLEX_URL="http://plex:32400")
    set_config(PLEX_TOKEN="token")
    set_config(PLEX_PUBLIC_URL="")
    set_config(PLEX_PATH_MAP=[])


@pytest.fixture
def jellyfin():
    set_config(JELLYFIN_URL="http://jellyfin:8096")
    set_config(JELLYFIN_API_KEY="key")
    set_config(JELLYFIN_PUBLIC_URL="")
    set_config(JELLYFIN_PATH_MAP=[])


@pytest.fixture
def arrs():
    """Both *arrs addressable. No key: a link to a page is not a call."""
    set_config(RADARR_URL="http://radarr:7878")
    set_config(SONARR_URL="http://sonarr:8989")


@pytest.fixture
def answers(monkeypatch):
    """Serve canned answers by URL and record what was asked. Both modules are
    patched: media_server fetches the section list, links the rest."""
    asked: list[str] = []
    canned: dict[str, object] = {}

    def fake_request(url, headers=None, payload=None, timeout=30, method=None):
        asked.append(url)
        # Longest prefix first: /library/sections is a prefix of every section
        # listing, and the section list is not what a search asks for.
        for prefix in sorted(canned, key=len, reverse=True):
            if url.startswith(prefix):
                answer = canned[prefix]
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return None

    monkeypatch.setattr(links, "request", fake_request)
    monkeypatch.setattr(media_server, "request", fake_request)
    canned["http://plex:32400/library/sections"] = PLEX_SECTIONS
    canned["http://plex:32400/identity"] = IDENTITY
    canned["http://jellyfin:8096/System/Info"] = {"Id": "server-1"}
    return asked, canned


def test_a_film_is_found_by_the_file_plex_lists(plex, answers):
    asked, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("413299", "Alien", 1979, "/data/media/movies/Alien (1979)/Alien.mkv")
    )

    found = find("/data/media/movies/Alien (1979)", "Alien", 1979)

    assert found == [
        {
            "server": "plex",
            "label": "Plex",
            "url": (
                "http://plex:32400/web/index.html#!/server/abc123"
                "/details?key=%2Flibrary%2Fmetadata%2F413299"
            ),
        }
    ]
    # The section holding the folder, searched by name rather than listed whole.
    assert any("/library/sections/1/all?title=Alien" in url for url in asked)


def test_a_series_is_confirmed_by_its_own_folders(plex, answers):
    """A show's listing entry carries no paths and no parameter adds them, so
    the one candidate is asked for its own metadata."""
    asked, canned = answers
    canned["http://plex:32400/library/sections/2/all"] = container(
        plex_show("415641", "30 Rock", 2006)
    )
    canned["http://plex:32400/library/metadata/415641"] = {
        "MediaContainer": {
            "Metadata": [{"Location": [{"path": "/data/media/tv/30 Rock (2006)"}]}]
        }
    }

    found = find("/data/media/tv/30 Rock (2006)", "30 Rock", 2006)

    assert found[0]["url"].endswith("details?key=%2Flibrary%2Fmetadata%2F415641")
    assert "http://plex:32400/library/metadata/415641" in asked


def test_the_public_address_is_what_the_link_is_built_on(plex, answers):
    """The address we call Plex on is this container's, and a phone cannot
    follow it. Only the link moves; the lookup still goes to the other."""
    set_config(PLEX_PUBLIC_URL="https://plex.example.com")
    _, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien", 1979, "/data/media/movies/Alien (1979)/Alien.mkv")
    )

    found = find("/data/media/movies/Alien (1979)", "Alien", 1979)

    assert found[0]["url"].startswith("https://plex.example.com/web/index.html#!/server/abc123")


def test_our_paths_are_mapped_to_the_ones_plex_indexes(plex, answers, monkeypatch):
    set_config(PLEX_PATH_MAP=[("/data/media", "/srv/media")])
    monkeypatch.setitem(
        PLEX_SECTIONS["MediaContainer"]["Directory"][0],
        "Location",
        [{"path": "/srv/media/movies"}],
    )
    _, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien", 1979, "/srv/media/movies/Alien (1979)/Alien.mkv")
    )

    found = find("/data/media/movies/Alien (1979)", "Alien", 1979)

    assert found and found[0]["server"] == "plex"


def test_a_folder_plex_does_not_index_is_never_searched(plex, answers):
    asked, _ = answers

    assert find("/elsewhere/Alien (1979)", "Alien", 1979) == []
    assert asked == ["http://plex:32400/library/sections"]


def test_a_longer_neighbour_does_not_answer_for_the_title(plex, answers):
    """`title` matches on a prefix, so searching for Alien turns up Aliens
    too. The path is what settles it."""
    _, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien", 1979, "/data/media/movies/Alien (1979)/Alien.mkv"),
        plex_movie("2", "Aliens", 1986, "/data/media/movies/Aliens (1986)/Aliens.mkv"),
    )

    found = find("/data/media/movies/Aliens (1986)", "Aliens", 1986)

    assert found[0]["url"].endswith("key=%2Flibrary%2Fmetadata%2F2")


def test_the_name_and_year_answer_when_no_path_does(plex, answers):
    """The server spells the library differently and nothing maps it, which
    is a link worth offering rather than a mystery."""
    _, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("7", "Alien", 1979, "/somewhere/else/Alien.mkv")
    )

    found = find("/data/media/movies/Alien (1979)", "Alien", 1979)

    assert found[0]["url"].endswith("key=%2Flibrary%2Fmetadata%2F7")


def test_two_of_one_name_and_year_are_not_guessed_between(plex, answers):
    _, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("7", "Alien", 1979, "/somewhere/else/Alien.mkv"),
        plex_movie("8", "Alien", 1979, "/somewhere/other/Alien.mkv"),
    )

    assert find("/data/media/movies/Alien (1979)", "Alien", 1979) == []


def test_a_different_year_is_a_different_film(plex, answers):
    _, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("7", "Alien", 2026, "/somewhere/else/Alien.mkv")
    )

    assert find("/data/media/movies/Alien (1979)", "Alien", 1979) == []


def test_a_name_the_server_spells_differently_is_searched_again(plex, answers):
    """Sonarr disambiguates with a parenthetical Plex has never heard of. The
    part before it still finds the show, and the folder confirms it."""
    _, canned = answers
    canned["http://plex:32400/library/sections/2/all?title=The+Traitors+%28US%29"] = container()
    canned["http://plex:32400/library/sections/2/all?title=The+Traitors"] = container(
        plex_show("11", "The Traitors", 2023)
    )
    canned["http://plex:32400/library/metadata/11"] = {
        "MediaContainer": {
            "Metadata": [{"Location": [{"path": "/data/media/tv/The Traitors (US) (2023)"}]}]
        }
    }

    found = find("/data/media/tv/The Traitors (US) (2023)", "The Traitors (US)", 2023)

    assert found[0]["url"].endswith("key=%2Flibrary%2Fmetadata%2F11")


def test_the_second_search_takes_no_answer_but_the_folder(plex, answers):
    """The shorter search is a wider one, so the same-country neighbour it
    turns up must not be able to answer on its name."""
    _, canned = answers
    canned["http://plex:32400/library/sections/2/all?title=The+Traitors+%28US%29"] = container()
    canned["http://plex:32400/library/sections/2/all?title=The+Traitors"] = container(
        plex_show("12", "The Traitors Australia", 2022)
    )
    canned["http://plex:32400/library/metadata/12"] = {
        "MediaContainer": {
            "Metadata": [{"Location": [{"path": "/data/media/tv/The Traitors (AU) (2022)"}]}]
        }
    }

    assert find("/data/media/tv/The Traitors (US) (2023)", "The Traitors (US)", 2023) == []


def test_jellyfin_is_found_by_the_path_it_reports(jellyfin, answers):
    asked, canned = answers
    canned["http://jellyfin:8096/Items?"] = {
        "Items": [
            {
                "Id": "9f2",
                "Name": "30 Rock",
                "ProductionYear": 2006,
                "Path": "/data/media/tv/30 Rock (2006)",
            }
        ]
    }

    found = find("/data/media/tv/30 Rock (2006)", "30 Rock", 2006)

    assert found == [
        {
            "server": "jellyfin",
            "label": "Jellyfin",
            "url": "http://jellyfin:8096/web/#/details?id=9f2&serverId=server-1",
        }
    ]
    assert any("SearchTerm=30+Rock" in url for url in asked)


def test_plex_offers_no_link_without_the_id_its_web_app_routes_on(plex, answers):
    """Unlike Jellyfin's, the Plex route carries the machine id in the middle
    of it. Without one there is no link to build, only a broken one."""
    _, canned = answers
    canned["http://plex:32400/identity"] = {}
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien", 1979, "/data/media/movies/Alien (1979)/Alien.mkv")
    )

    assert find("/data/media/movies/Alien (1979)", "Alien", 1979) == []


def test_the_servers_own_id_is_asked_for_once(plex, answers):
    """It is static for the life of the server and every link needs it, so a
    grid of a hundred posters must not be a hundred extra calls."""
    asked, canned = answers
    canned["http://plex:32400/library/sections/1/all?title=Alien"] = container(
        plex_movie("1", "Alien", 1979, "/data/media/movies/Alien (1979)/Alien.mkv")
    )
    canned["http://plex:32400/library/sections/1/all?title=Aliens"] = container(
        plex_movie("2", "Aliens", 1986, "/data/media/movies/Aliens (1986)/Aliens.mkv")
    )

    assert find("/data/media/movies/Alien (1979)", "Alien", 1979)
    assert find("/data/media/movies/Aliens (1986)", "Aliens", 1986)
    assert asked.count("http://plex:32400/identity") == 1


def test_a_title_jellyfin_does_not_have_is_no_link(jellyfin, answers):
    _, canned = answers
    canned["http://jellyfin:8096/Items?"] = {"Items": []}

    assert find("/data/media/tv/30 Rock (2006)", "30 Rock", 2006) == []


def test_jellyfin_links_without_a_server_id_it_could_not_read(jellyfin, answers):
    _, canned = answers
    canned["http://jellyfin:8096/System/Info"] = urllib.error.URLError("refused")
    canned["http://jellyfin:8096/Items?"] = {
        "Items": [{"Id": "9f2", "Name": "A", "Path": "/data/media/tv/A"}]
    }

    found = find("/data/media/tv/A", "A", None)

    # The item id is what the page is addressed by; the server id only says
    # which of several a client is looking at.
    assert found[0]["url"] == "http://jellyfin:8096/web/#/details?id=9f2"


def test_a_server_that_will_not_answer_costs_the_link_and_nothing_else(plex, answers):
    _, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = urllib.error.URLError("refused")

    assert find("/data/media/movies/Alien (1979)", "Alien", 1979) == []


def test_unconfigured_servers_are_never_called(answers):
    asked, _ = answers
    for name in ("PLEX_URL", "PLEX_TOKEN", "JELLYFIN_URL", "JELLYFIN_API_KEY"):
        set_config(**{name: ""})

    assert find("/data/media/movies/Alien (1979)", "Alien", 1979) == []
    assert asked == []


def test_a_media_server_switched_off_mid_request_resolves_to_nothing(answers):
    """for_title reads the settings before it asks, but a save between the two
    reloads config underneath the request. Each lookup checks its own address
    rather than trusting the gate it came through."""
    asked, _ = answers
    subject = links.Subject("/data/media/movies/Alien (1979)", "Alien", 1979)
    unasked = [server for server in links.SERVERS if not server.known]

    assert [server.resolve(subject) for server in unasked] == ["", ""]
    assert asked == []


def test_a_title_with_no_folder_has_nothing_to_look_up(plex, answers):
    """The folder is the fact every match is settled against; a title with
    none could only be matched on a name, which is how the wrong film gets
    linked."""
    asked, _ = answers

    assert links.for_title(links.Subject("")) == []
    assert asked == []


def test_a_title_with_no_name_is_searched_for_under_its_folder(plex, answers):
    """A folder no *arr claims is named after itself and nothing else."""
    asked, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien (1979)", 1979, "/data/media/movies/Alien (1979)/Alien.mkv")
    )

    found = links.for_title(links.Subject("/data/media/movies/Alien (1979)"))

    assert found[0]["server"] == "plex"
    assert any("title=Alien+%281979%29" in url for url in asked)


def test_a_resolved_link_is_not_looked_up_twice(plex, answers):
    asked, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien", 1979, "/data/media/movies/Alien (1979)/Alien.mkv")
    )

    first = find("/data/media/movies/Alien (1979)", "Alien", 1979)
    calls = len(asked)
    second = find("/data/media/movies/Alien (1979)", "Alien", 1979)

    assert first == second
    assert len(asked) == calls


def test_a_title_the_server_did_not_have_is_asked_about_again(plex, answers):
    """Nothing found is also what a server that was down at the time says, so
    a miss is remembered for a minute rather than the quarter hour a hit gets."""
    asked, canned = answers
    folder = "/data/media/movies/Alien (1979)"
    canned["http://plex:32400/library/sections/1/all"] = container()

    assert find(folder, "Alien", 1979) == []
    calls = len(asked)
    assert find(folder, "Alien", 1979) == []
    assert len(asked) == calls, "and remembered while it is fresh"

    # The miss aged past its minute, and Plex has come back since.
    with links._found_lock:
        stamp, url = links._found[("plex", folder)]
        links._found[("plex", folder)] = (stamp - links.MISSING_TTL - 1, url)
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien", 1979, f"{folder}/Alien.mkv")
    )

    assert find(folder, "Alien", 1979)[0]["server"] == "plex"


def test_forgetting_sends_the_next_ask_back_to_the_server(plex, answers):
    asked, canned = answers
    canned["http://plex:32400/library/sections/1/all"] = container(
        plex_movie("1", "Alien", 1979, "/data/media/movies/Alien (1979)/Alien.mkv")
    )

    find("/data/media/movies/Alien (1979)", "Alien", 1979)
    links.forget()
    calls = len(asked)
    find("/data/media/movies/Alien (1979)", "Alien", 1979)

    assert len(asked) > calls


def test_a_film_links_to_radarr_by_the_slug_it_came_with(arrs, answers):
    """Radarr's own pages route on the slug, which for a film is the TMDB id
    it is spelled with. The title carried it here, so nothing is asked."""
    asked, _ = answers

    found = find("/data/media/movies/Dune (2024)", "Dune", 2024, arr="radarr", slug="693134")

    assert found == [
        {"server": "radarr", "label": "Radarr", "url": "http://radarr:7878/movie/693134"}
    ]
    assert asked == []


def test_a_series_links_to_sonarr_and_never_to_radarr(arrs, answers):
    """Only the *arr that claims the title answers for it: the other's page
    at that slug is a page that does not exist."""
    found = find(
        "/data/media/tv/Severance (2022)", "Severance", 2022, arr="sonarr", slug="severance"
    )

    assert found == [
        {"server": "sonarr", "label": "Sonarr", "url": "http://sonarr:8989/series/severance"}
    ]


def test_a_folder_no_arr_claims_links_to_neither(arrs, answers):
    assert find("/data/media/movies/Home Video (2019)", "Home Video") == []


def test_a_slug_is_escaped_into_the_arr_path(arrs, answers):
    found = find("/data/media/tv/A", "A", arr="sonarr", slug="a b/c")

    assert found[0]["url"] == "http://sonarr:8989/series/a%20b%2Fc"


def test_the_public_address_is_what_the_arr_link_is_built_on(arrs, answers):
    set_config(RADARR_PUBLIC_URL="https://radarr.example.com")

    found = find("/data/media/movies/Dune (2024)", "Dune", 2024, arr="radarr", slug="693134")

    assert found[0]["url"] == "https://radarr.example.com/movie/693134"


def test_an_arr_with_no_address_is_no_link(answers):
    set_config(SONARR_URL="http://sonarr:8989")

    assert find("/data/media/movies/Dune", "Dune", arr="radarr", slug="693134") == []


def test_a_title_links_to_imdb_by_the_id_the_arr_carried(answers):
    """The one entry here that needs nothing configured: IMDb is a website
    rather than an install, and the id arrived with the title."""
    asked, _ = answers

    found = find("/data/media/movies/Dune (2024)", "Dune", 2024, imdb_id="tt15239678")

    assert found == [
        {"server": "imdb", "label": "IMDb", "url": "https://www.imdb.com/title/tt15239678/"}
    ]
    assert asked == []


def test_a_title_with_no_imdb_id_gets_no_imdb_button(answers):
    """Which is every folder no *arr claims, and the few titles one claims
    without an id."""
    assert find("/data/media/movies/Home Video (2019)", "Home Video") == []


def test_something_that_is_not_an_imdb_id_is_refused(answers):
    """It reaches us from a foreign API and leaves in an href, so the shape
    is checked rather than trusted."""
    assert find("/data/media/movies/Dune (2024)", "Dune", imdb_id="javascript:alert(1)") == []
    assert find("/data/media/movies/Dune (2024)", "Dune", imdb_id="tt") == []


def test_what_is_offered_names_the_servers_and_hands_over_the_arr(plex, arrs, answers):
    """Naming a media server is free and finding a title inside it is not, so
    the sheet gets the name now and the link later. The *arr's link is the
    slug written into a path, so it is there from the start."""
    asked, _ = answers

    offered = links.offered(
        links.Subject("/data/media/movies/Dune (2024)", "Dune", 2024, "radarr", "693134")
    )

    assert offered == [
        {"server": "plex", "label": "Plex"},
        {"server": "radarr", "label": "Radarr", "url": "http://radarr:7878/movie/693134"},
    ]
    assert asked == []
