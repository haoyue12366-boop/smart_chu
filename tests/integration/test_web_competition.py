"""比赛初排/单菜追加在真实浏览器和固定发布后端上验收。"""

from tests.integration.test_web_browser import browser_checks


def test_real_task_competition_flow(record_testsuite_property):
    browser_checks(["tests/e2e/competition_flow.spec.ts"], record_testsuite_property)
