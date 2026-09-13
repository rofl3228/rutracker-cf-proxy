from rutracker_proxy.challenge import is_challenge


def test_real_challenge_page(challenge_html):
    assert is_challenge(403, {}, challenge_html)


def test_cf_mitigated_header_alone_is_enough():
    assert is_challenge(403, {"cf-mitigated": "challenge"}, b"")


def test_real_page_is_not_challenge(rutracker_html):
    assert not is_challenge(200, {"content-type": "text/html; charset=Windows-1251"}, rutracker_html)


def test_redirect_to_login_is_not_challenge():
    assert not is_challenge(302, {"location": "https://rutracker.org/forum/login.php"}, b"")


def test_site_own_403_is_not_challenge(rutracker_html):
    assert not is_challenge(403, {}, rutracker_html)


def test_challenge_markers_ignored_on_200(challenge_html):
    # e.g. a forum post quoting the challenge page must not trigger a refresh
    assert not is_challenge(200, {}, challenge_html)
