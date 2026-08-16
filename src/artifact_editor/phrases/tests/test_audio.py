import pytest

from artifact_editor.phrases import phrases

# test for simple_paginator(page: int, errors_only: bool, total_pages: int) -> str:
# from phrases.py

class TestSimplePaginator:
    def test_simple_paginator_basic(self):
        # there is only one page, and it is the current page.
        assert phrases.simple_paginator(1, False, 1)[1] == "< [1] >"
        assert phrases.simple_paginator(1, False, 5)[1] == "< [1] 2 3 4 5 >"
        assert phrases.simple_paginator(1, False, 11)[1] == "< [1] 2 3 4 5 6 7 8 9 10 11 >"
        assert phrases.simple_paginator(1, False, 12)[1] == "< [1] 2 3 4 ... 11 12 >"

        assert phrases.simple_paginator(5, False, 5)[1] == "< 1 2 3 4 [5] >"
        assert phrases.simple_paginator(5, False, 11)[1] == "< 1 2 3 4 [5] 6 7 8 9 10 11 >"
        assert phrases.simple_paginator(5, False, 12)[1] == "< 1 2 3 4 [5] 6 7 8 ... 11 12 >"
        assert phrases.simple_paginator(6, False, 12)[1] == "< 1 2 3 4 5 [6] 7 8 9 10 11 12 >"
        
        assert phrases.simple_paginator(8, False, 12)[1] == "< 1 2 ... 5 6 7 [8] 9 10 11 12 >"

        assert phrases.simple_paginator(10, False, 20)[1] == "< 1 2 ... 7 8 9 [10] 11 12 13 ... 19 20 >"
        assert phrases.simple_paginator(12, False, 13)[1] == "< 1 2 ... 9 10 11 [12] 13 >"
        assert phrases.simple_paginator(10, False, 20)[1] == "< 1 2 ... 7 8 9 [10] 11 12 13 ... 19 20 >"

    #def test_rough_edges(self):
        assert phrases.simple_paginator(7, False, 13)[1] == "< 1 2 3 4 5 6 [7] 8 9 10 11 12 13 >"