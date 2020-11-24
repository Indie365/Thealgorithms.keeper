import urllib.parse

import pytest
from _pytest.monkeypatch import MonkeyPatch
from gidgethub import apps, sansio

from algorithms_keeper import pull_requests, utils
from algorithms_keeper.constants import (
    CHECKBOX_NOT_TICKED_COMMENT,
    EMPTY_BODY_COMMENT,
    MAX_PR_REACHED_COMMENT,
    NO_EXTENSION_COMMENT,
    Label,
)

from .test_parser import get_file_code
from .utils import (
    CHECKBOX_NOT_TICKED,
    CHECKBOX_TICKED,
    CHECKBOX_TICKED_UPPER,
    MockGitHubAPI,
    check_run_url,
    comments_url,
    files_url,
    html_pr_url,
    issue_url,
    labels_url,
    mock_return,
    number,
    pr_url,
    pr_user_search_url,
    repository,
    reviewers_url,
    sha,
    user,
)

# Comment constants
EMPTY_BODY_COMMENT = EMPTY_BODY_COMMENT.format(user_login=user)
CHECKBOX_NOT_TICKED_COMMENT = CHECKBOX_NOT_TICKED_COMMENT.format(user_login=user)
NO_EXTENSION_COMMENT = NO_EXTENSION_COMMENT.format(user_login=user)


@pytest.fixture(scope="module", autouse=True)
def patch_module(monkeypatch=MonkeyPatch()):
    async def mock_get_file_content(*args, **kwargs):
        filename = kwargs["file"]["filename"]
        if filename in {
            "require_doctest.py",
            "require_annotations.py",
            "require_descriptive_names.py",
            "require_return_annotation.py",
            "no_errors.py",
        }:
            return get_file_code(filename)
        else:
            return ""

    monkeypatch.setattr(apps, "get_installation_access_token", mock_return)
    monkeypatch.setattr(utils, "get_file_content", mock_get_file_content)
    yield monkeypatch
    monkeypatch.undo()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, comment",
    (
        ("", EMPTY_BODY_COMMENT),
        (CHECKBOX_NOT_TICKED, CHECKBOX_NOT_TICKED_COMMENT),
    ),
)
async def test_pr_opened_no_body_and_no_ticked(body, comment):
    data = {
        "action": "opened",
        "pull_request": {
            "url": pr_url,
            "body": body,
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "requested_reviewers": [{"login": "test1"}, {"login": "test2"}],
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None, comments_url: None}
    patch = {pr_url: None}
    delete = {reviewers_url: None}
    gh = MockGitHubAPI(post=post, patch=patch, delete=delete)
    await pull_requests.router.dispatch(event, gh)
    assert len(gh.post_url) == 2
    assert comments_url in gh.post_url
    assert labels_url in gh.post_url
    assert {"body": comment} in gh.post_data
    assert {"labels": [Label.INVALID]} in gh.post_data
    assert pr_url in gh.patch_url
    assert {"state": "closed"} in gh.patch_data
    assert reviewers_url in gh.delete_url
    assert {"reviewers": ["test1", "test2"]} in gh.delete_data


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("opened", "synchronize"))
async def test_pr_opened_in_draft_mode(action):
    data = {
        "action": action,
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "requested_reviewers": [{"login": "test1"}, {"login": "test2"}],
            "draft": True,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    getiter = {
        pr_user_search_url: {
            "total_count": 1,
            "items": [
                {"number": 1, "state": "opened"},
            ],
        },
    }
    gh = MockGitHubAPI(getiter=getiter)
    await pull_requests.router.dispatch(event, gh)
    # PRs should be closed if they are invalid even if it is opened in draft mode
    if action == "opened":
        assert pr_user_search_url in gh.getiter_url
    else:
        assert gh.getiter_url == []
    assert gh.post_url == []
    assert gh.post_data == []
    assert gh.patch_url == []
    assert gh.patch_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_pr_opened_by_member():
    data = {
        "action": "opened",
        "pull_request": {
            "url": pr_url,
            "body": "",  # body can be empty for member
            "labels": [],
            "user": {"login": user},
            "author_association": "MEMBER",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "requested_reviewers": [{"login": "test1"}, {"login": "test2"}],
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None}
    getiter = {files_url: []}  # for check_pr_files function
    gh = MockGitHubAPI(getiter=getiter, post=post)
    await pull_requests.router.dispatch(event, gh)
    assert files_url in gh.getiter_url
    assert labels_url in gh.post_url
    assert {"labels": [Label.AWAITING_REVIEW]} in gh.post_data
    assert gh.patch_url == []
    assert gh.patch_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_max_pr_reached():
    data = {
        "action": "opened",
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED_UPPER,  # Case doesn't matter
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "requested_reviewers": [{"login": "test1"}, {"login": "test2"}],
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    getiter = {
        pr_user_search_url: {"total_count": 2, "items": [{"number": 1}, {"number": 2}]},
        files_url: [],  # for check_pr_files function
    }
    post = {comments_url: None}
    patch = {pr_url: None}
    delete = {reviewers_url: None}
    gh = MockGitHubAPI(getiter=getiter, post=post, patch=patch, delete=delete)
    await pull_requests.router.dispatch(event, gh)
    assert pr_user_search_url in gh.getiter_url
    assert comments_url in gh.post_url
    assert {
        "body": MAX_PR_REACHED_COMMENT.format(user_login=user, pr_number="#1, #2")
    } in gh.post_data
    assert pr_url in gh.patch_url
    assert reviewers_url in gh.delete_url
    assert {"reviewers": ["test1", "test2"]} in gh.delete_data


@pytest.mark.asyncio
async def test_max_pr_disabled(monkeypatch):
    monkeypatch.setattr(pull_requests, "MAX_PR_PER_USER", 0)
    data = {
        "action": "opened",
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED_UPPER,  # Case doesn't matter
            "labels": [],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None}
    getiter = {files_url: []}
    gh = MockGitHubAPI(getiter=getiter, post=post)
    await pull_requests.router.dispatch(event, gh)
    assert files_url in gh.getiter_url
    # No changes as max pr checks are disabled
    assert labels_url in gh.post_url
    assert {"labels": [Label.AWAITING_REVIEW]} in gh.post_data
    assert gh.patch_url == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, getiter",
    (
        (
            "opened",
            {
                pr_user_search_url: {"total_count": 1, "items": [{"number": 1}]},
                files_url: [
                    {"filename": "newton.py", "contents_url": "", "status": "added"},
                    {"filename": "fibonacci", "contents_url": "", "status": "added"},
                ],
            },
        ),
        (
            "synchronize",
            {
                files_url: [
                    {"filename": "newton.py", "contents_url": "", "status": "added"},
                    {"filename": "fibonacci", "contents_url": "", "status": "added"},
                ],
            },
        ),
    ),
)
async def test_for_extensionless_files(action, getiter):
    data = {
        "action": action,
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            "labels": [],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "requested_reviewers": [{"login": "test1"}, {"login": "test2"}],
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {comments_url: None, labels_url: None}
    patch = {pr_url: None}
    delete = {reviewers_url: None}
    gh = MockGitHubAPI(getiter=getiter, post=post, patch=patch, delete=delete)
    await pull_requests.router.dispatch(event, gh)
    if event.data["action"] == "opened":
        assert len(gh.getiter_url) == 2
        assert pr_user_search_url in gh.getiter_url
        assert files_url in gh.getiter_url
        assert len(gh.post_url) == 3  # Two labels and one comment.
        assert {"labels": [Label.AWAITING_REVIEW]} in gh.post_data
    elif event.data["action"] == "synchronize":
        assert len(gh.getiter_url) == 1
        assert files_url in gh.getiter_url
        assert len(gh.post_url) == 2  # AWAITING_REVIEW label is not added.
    assert comments_url in gh.post_url
    assert labels_url in gh.post_url
    assert {"body": NO_EXTENSION_COMMENT} in gh.post_data
    assert {"labels": [Label.INVALID]} in gh.post_data
    assert pr_url in gh.patch_url
    assert {"state": "closed"} in gh.patch_data
    assert reviewers_url in gh.delete_url
    assert {"reviewers": ["test1", "test2"]} in gh.delete_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, getiter",
    (
        (
            "opened",
            {
                pr_user_search_url: {"total_count": 1, "items": [{"number": 1}]},
                files_url: [
                    {
                        "filename": ".travis.yml",
                        "contents_url": "",
                        "status": "modified",
                    },
                    {"filename": "README.md", "contents_url": "", "status": "modified"},
                    {
                        "filename": "pytest.ini",
                        "contents_url": "",
                        "status": "modified",
                    },
                    # Add an extensionless file in the `github` directory which
                    # should be ignored.
                    {
                        "filename": ".github/CODEOWNERS",
                        "contents_url": "",
                        "status": "modified",
                    },
                ],
            },
        ),
        (
            "synchronize",
            {
                files_url: [
                    {
                        "filename": ".travis.yml",
                        "contents_url": "",
                        "status": "modified",
                    },
                    {"filename": "README.md", "contents_url": "", "status": "modified"},
                    # We will add one `__` Python file in the mix which should be
                    # ignored.
                    {"filename": "__init__.py", "contents_url": "", "status": "added"},
                ],
            },
        ),
    ),
)
async def test_pr_with_no_python_files(action, getiter):
    data = {
        "action": action,
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            "labels": [],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None}
    gh = MockGitHubAPI(getiter=getiter, post=post)
    await pull_requests.router.dispatch(event, gh)
    if data["action"] == "opened":
        assert len(gh.getiter_url) == 2
        assert pr_user_search_url in gh.getiter_url
        assert files_url in gh.getiter_url
        assert labels_url in gh.post_url
        assert {"labels": [Label.AWAITING_REVIEW]} in gh.post_data
    elif data["action"] == "synchronize":
        assert len(gh.getiter_url) == 1
        assert files_url in gh.getiter_url
        assert gh.post_url == []
        assert gh.post_data == []
    # Nothing happens as there are no Python files
    assert gh.patch_url == []
    assert gh.patch_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


# From this point on, as we have tested the `PullRequestFilesParser` in a separate
# file we will assume that the logic is correct for the class. With that in mind,
# we will only test the logic of the `check_pr_files` function and not whether the
# parser is working accordingly or not.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, getiter",
    (
        (
            "opened",
            {
                pr_user_search_url: {"total_count": 1, "items": [{"number": 1}]},
                files_url: [
                    {
                        "filename": "require_doctest.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {"filename": "test_algo.py", "contents_url": "", "status": "added"},
                    {
                        "filename": "require_annotations.py",
                        "contents_url": "",
                        "status": "added",
                    },
                ],
            },
        ),
        (
            "synchronize",
            {
                files_url: [
                    {
                        "filename": "require_doctest.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {"filename": "test_algo.py", "contents_url": "", "status": "added"},
                    {
                        "filename": "require_annotations.py",
                        "contents_url": "",
                        "status": "added",
                    },
                ],
            },
        ),
    ),
)
async def test_pr_with_test_file(action, getiter):
    remove_label = urllib.parse.quote(Label.REQUIRE_TEST)
    data = {
        "action": action,
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            # This is like a marker to test the function, the label should be removed.
            "labels": [{"name": Label.REQUIRE_TEST}],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None, comments_url: None}
    delete = {f"{labels_url}/{remove_label}": None}
    gh = MockGitHubAPI(getiter=getiter, post=post, delete=delete)
    await pull_requests.router.dispatch(event, gh)
    if data["action"] == "opened":
        assert len(gh.getiter_url) == 2
        assert pr_user_search_url in gh.getiter_url
        assert files_url in gh.getiter_url
        assert len(gh.post_url) == 3  # Two labels and one comment.
        assert comments_url in gh.post_url
        assert labels_url in gh.post_url
        assert {"labels": [Label.AWAITING_REVIEW]} in gh.post_data
    elif data["action"] == "synchronize":
        assert len(gh.getiter_url) == 1
        assert gh.getiter_url[0] == files_url
        assert len(gh.post_url) == 1
        # No comment is posted in `synchronize`
        assert gh.post_url == [labels_url]
    assert {"labels": [Label.ANNOTATIONS]} in gh.post_data
    assert gh.delete_url[0] == f"{labels_url}/{remove_label}"
    assert gh.delete_data == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, getiter",
    (
        (
            "opened",
            {
                pr_user_search_url: {"total_count": 1, "items": [{"number": 1}]},
                files_url: [
                    {"filename": "no_errors.py", "contents_url": "", "status": "added"},
                    {"filename": "algorithm.py", "contents_url": "", "status": "added"},
                ],
            },
        ),
        (
            "synchronize",
            {
                files_url: [
                    {"filename": "no_errors.py", "contents_url": "", "status": "added"},
                    {"filename": "algorithm.py", "contents_url": "", "status": "added"},
                ],
            },
        ),
    ),
)
async def test_pr_with_successful_tests(action, getiter):
    data = {
        "action": action,
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            "labels": [],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None}
    gh = MockGitHubAPI(getiter=getiter, post=post)
    await pull_requests.router.dispatch(event, gh)
    if data["action"] == "opened":
        assert len(gh.getiter_url) == 2
        assert gh.getiter_url == [pr_user_search_url, files_url]
        assert labels_url in gh.post_url
        assert {"labels": [Label.AWAITING_REVIEW]} in gh.post_data
    elif data["action"] == "synchronize":
        assert len(gh.getiter_url) == 1
        assert gh.getiter_url[0] == files_url
        assert gh.post_url == []
        assert gh.post_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, getiter",
    (
        (
            "opened",
            {
                pr_user_search_url: {"total_count": 1, "items": [{"number": 1}]},
                files_url: [
                    {
                        "filename": "require_doctest.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {
                        "filename": "require_descriptive_names.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {
                        "filename": "require_annotations.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {
                        "filename": "require_return_annotation.py",
                        "contents_url": "",
                        "status": "added",
                    },
                ],
            },
        ),
        (
            "synchronize",
            {
                files_url: [
                    {
                        "filename": "require_doctest.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {
                        "filename": "require_descriptive_names.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {
                        "filename": "require_annotations.py",
                        "contents_url": "",
                        "status": "added",
                    },
                    {
                        "filename": "require_return_annotation.py",
                        "contents_url": "",
                        "status": "added",
                    },
                ],
            },
        ),
    ),
)
async def test_pr_with_add_all_require_labels(action, getiter):
    data = {
        "action": action,
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            "labels": [],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None, comments_url: None}
    gh = MockGitHubAPI(getiter=getiter, post=post)
    await pull_requests.router.dispatch(event, gh)
    if data["action"] == "opened":
        assert len(gh.getiter_url) == 2
        assert pr_user_search_url in gh.getiter_url
        assert files_url in gh.getiter_url
        assert len(gh.post_url) == 3
        assert labels_url in gh.post_url
        assert comments_url in gh.post_url
        assert {"labels": [Label.AWAITING_REVIEW]}
    elif data["action"] == "synchronize":
        assert len(gh.getiter_url) == 1
        assert files_url in gh.getiter_url
        assert len(gh.post_url) == 1
        # No comment is posted in `synchronize`
        assert labels_url in gh.post_url
    assert {
        "labels": [Label.REQUIRE_TEST, Label.DESCRIPTIVE_NAMES, Label.ANNOTATIONS]
    } in gh.post_data
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_pr_with_remove_all_require_labels():
    # This case will only be true when the action is `synchronize`
    test_label_url = labels_url + f"/{urllib.parse.quote(Label.REQUIRE_TEST)}"
    names_label_url = labels_url + f"/{urllib.parse.quote(Label.DESCRIPTIVE_NAMES)}"
    annotation_label_url = labels_url + f"/{urllib.parse.quote(Label.ANNOTATIONS)}"
    data = {
        "action": "synchronize",
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            "labels": [
                {"name": Label.REQUIRE_TEST},
                {"name": Label.DESCRIPTIVE_NAMES},
                {"name": Label.ANNOTATIONS},
            ],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    getiter = {
        files_url: [
            {"filename": "no_errors.py", "contents_url": "", "status": "added"},
            {"filename": "algorithm.py", "contents_url": "", "status": "added"},
        ],
    }
    delete = {test_label_url: None, names_label_url: None, annotation_label_url: None}
    gh = MockGitHubAPI(getiter=getiter, delete=delete)
    await pull_requests.router.dispatch(event, gh)
    assert len(gh.getiter_url) == 1
    assert files_url in gh.getiter_url
    # No labels are added
    assert gh.post_url == []
    assert gh.post_data == []
    # All labels are deleted
    assert test_label_url in gh.delete_url
    assert names_label_url in gh.delete_url
    assert annotation_label_url in gh.delete_url
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_label_on_ready_for_review_pr():
    # Open a PR in draft
    # Convert the draft PR to ready for review PR
    # Tests are failing on the latest commit, so test that it adds the label
    data = {
        "action": "ready_for_review",
        "pull_request": {
            "url": pr_url,
            "body": CHECKBOX_TICKED,
            "head": {"sha": sha},
            "labels": [],
            "user": {"login": user},
            "author_association": "NONE",
            "comments_url": comments_url,
            "issue_url": issue_url,
            "html_url": html_pr_url,
            "requested_reviewers": [{"login": "test1"}, {"login": "test2"}],
            "draft": False,
        },
        "repository": {"full_name": repository},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    getitem = {
        check_run_url: {
            "total_count": 2,
            "check_runs": [
                {"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "failure"},
            ],
        },
    }
    getiter = {files_url: []}
    post = {labels_url: None}
    gh = MockGitHubAPI(getitem=getitem, getiter=getiter, post=post)
    await pull_requests.router.dispatch(event, gh)
    assert len(gh.getitem_url) == 1
    assert check_run_url in gh.getitem_url
    assert len(gh.getiter_url) == 1
    assert files_url in gh.getiter_url
    assert labels_url in gh.post_url
    assert {"labels": [Label.FAILED_TEST]} in gh.post_data
    assert gh.delete_url == []


@pytest.mark.parametrize("state", ("commented", "changes_requested", "approved"))
async def test_pr_review_by_non_member(state):
    data = {
        "action": "submitted",
        "review": {
            "state": state,
            "author_association": "NONE",
        },
        "pull_request": {},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request_review", delivery_id="1")
    gh = MockGitHubAPI()
    await pull_requests.router.dispatch(event, gh)
    assert gh.post_url == []
    assert gh.post_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
@pytest.mark.parametrize("labels", ([], [{"name": Label.CHANGES_REQUESTED}]))
async def test_pr_review_changes_requested(labels):
    data = {
        "action": "submitted",
        "review": {
            "state": "changes_requested",
            "author_association": "MEMBER",
        },
        "pull_request": {
            "labels": labels,
            "issue_url": issue_url,
        },
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request_review", delivery_id="1")
    post = {labels_url: None}
    gh = MockGitHubAPI(post=post)
    await pull_requests.router.dispatch(event, gh)
    if not labels:
        assert labels_url in gh.post_url
        assert {"labels": [Label.CHANGES_REQUESTED]} in gh.post_data
    else:
        assert gh.post_url == []
        assert gh.post_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
@pytest.mark.parametrize("labels", ([], [{"name": Label.CHANGES_REQUESTED}]))
async def test_pr_review_approved(labels):
    remove_label = urllib.parse.quote(Label.CHANGES_REQUESTED)
    data = {
        "action": "submitted",
        "review": {
            "state": "approved",
            "author_association": "MEMBER",
        },
        "pull_request": {
            "labels": labels,
            "issue_url": issue_url,
        },
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request_review", delivery_id="1")
    delete = {f"{labels_url}/{remove_label}": None}
    gh = MockGitHubAPI(delete=delete)
    await pull_requests.router.dispatch(event, gh)
    if labels:
        assert f"{labels_url}/{remove_label}" in gh.delete_url
        assert gh.delete_data == []
    else:
        assert gh.delete_url == []
        assert gh.delete_data == []
    assert gh.post_url == []
    assert gh.post_data == []


@pytest.mark.asyncio
@pytest.mark.parametrize("labels", ([], [{"name": Label.AWAITING_REVIEW}]))
async def test_pr_review_approved_without_any_changes(labels):
    # Issue #10
    remove_label = urllib.parse.quote(Label.AWAITING_REVIEW)
    data = {
        "action": "submitted",
        "review": {
            "state": "approved",
            "author_association": "MEMBER",
        },
        "pull_request": {
            "labels": labels,
            "issue_url": issue_url,
        },
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request_review", delivery_id="1")
    delete = {f"{labels_url}/{remove_label}": None}
    gh = MockGitHubAPI(delete=delete)
    await pull_requests.router.dispatch(event, gh)
    if labels:
        assert f"{labels_url}/{remove_label}" in gh.delete_url
        assert gh.delete_data == []
    else:
        assert gh.delete_url == []
        assert gh.delete_data == []
    assert gh.post_url == []
    assert gh.post_data == []


# Test conditions for when to add and remove `Label.AWAITING_REVIEW` label:
# NOTE: All conditions assumes the PR has been already been labeled AWAITING_REVIEW when
#       it was opened.
# 1. PR opened with no errors (No error labels were added)
# 2. PR opened with errors (Error labels were added)
# 3. One or more label from PR_NOT_READY_LABELS were removed but not all
# 4. All labels from PR present in PR_NOT_READY_LABELS were removed
# 5. CHANGES_REQUESTED label was added (PR was reviewed)
# 6. CHANGES_REQUESTED label was removed (PR was approved)


@pytest.mark.asyncio
async def test_pr_opened_with_no_errors_and_labeled():
    data = {
        "action": "labeled",
        "pull_request": {
            "labels": [{"name": Label.AWAITING_REVIEW}],
            "issue_url": issue_url,
        },
        "label": {"name": Label.AWAITING_REVIEW},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    gh = MockGitHubAPI()
    await pull_requests.router.dispatch(event, gh)
    # No label is added or removed.
    assert gh.post_url == []
    assert gh.post_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_pr_opened_with_errors_and_labeled():
    remove_label = urllib.parse.quote(Label.AWAITING_REVIEW)
    data = {
        "action": "labeled",
        "pull_request": {
            "labels": [{"name": Label.AWAITING_REVIEW}, {"name": Label.REQUIRE_TEST}],
            "issue_url": issue_url,
        },
        "label": {"name": Label.REQUIRE_TEST},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    delete = {f"{labels_url}/{remove_label}": None}
    gh = MockGitHubAPI(delete=delete)
    await pull_requests.router.dispatch(event, gh)
    # No labels were added.
    assert gh.post_url == []
    assert gh.post_data == []
    # AWAITING_REVIEW label was removed.
    assert f"{labels_url}/{remove_label}" in gh.delete_url
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_pr_not_all_labels_removed():
    data = {
        "action": "unlabeled",
        "pull_request": {
            "labels": [{"name": Label.REQUIRE_TEST}],
            "issue_url": issue_url,
        },
        "label": {"name": Label.ANNOTATIONS},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    gh = MockGitHubAPI()
    await pull_requests.router.dispatch(event, gh)
    # No label is added or removed.
    assert gh.post_url == []
    assert gh.post_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_pr_all_labels_removed():
    data = {
        "action": "unlabeled",
        "pull_request": {
            "labels": [{"name": "good first issue"}],  # Random label.
            "issue_url": issue_url,
        },
        "label": {"name": Label.ANNOTATIONS},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    post = {labels_url: None}
    gh = MockGitHubAPI(post=post)
    await pull_requests.router.dispatch(event, gh)
    # No error labels so the AWAITING_REVIEW label should be added.
    assert labels_url in gh.post_url
    assert {"labels": [Label.AWAITING_REVIEW]} in gh.post_data
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_changes_requested_label_added():
    remove_label = urllib.parse.quote(Label.AWAITING_REVIEW)
    data = {
        "action": "labeled",
        "pull_request": {
            "labels": [
                {"name": Label.CHANGES_REQUESTED},
                {"name": Label.AWAITING_REVIEW},
            ],
            "issue_url": issue_url,
        },
        "label": {"name": Label.CHANGES_REQUESTED},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    delete = {f"{labels_url}/{remove_label}": None}
    gh = MockGitHubAPI(delete=delete)
    await pull_requests.router.dispatch(event, gh)
    assert f"{labels_url}/{remove_label}" in gh.delete_url
    assert gh.delete_data == []
    assert gh.post_url == []
    assert gh.post_data == []


@pytest.mark.asyncio
async def test_changes_requested_label_removed():
    data = {
        "action": "unlabeled",
        "pull_request": {
            "labels": [],
            "issue_url": issue_url,
        },
        "label": {"name": Label.CHANGES_REQUESTED},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    gh = MockGitHubAPI()
    await pull_requests.router.dispatch(event, gh)
    # No label is added or removed.
    assert gh.post_url == []
    assert gh.post_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []


@pytest.mark.asyncio
async def test_awaiting_review_label_removed():
    # Issue #10
    data = {
        "action": "unlabeled",
        "pull_request": {
            "labels": [],
            "issue_url": issue_url,
        },
        "label": {"name": Label.AWAITING_REVIEW},
        "installation": {"id": number},
    }
    event = sansio.Event(data, event="pull_request", delivery_id="1")
    gh = MockGitHubAPI()
    await pull_requests.router.dispatch(event, gh)
    # No label is added or removed.
    assert gh.post_url == []
    assert gh.post_data == []
    assert gh.delete_url == []
    assert gh.delete_data == []
