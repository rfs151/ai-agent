from app.search import Index, tokens


def test_tokens_mix_chinese_and_ascii():
    assert tokens("告警录像 MP4") == ["mp4", "告警", "警录", "录像"]


def test_ranking_and_project_filter():
    ix = Index([
        {"project": "vision", "title": "告警录像", "url": "u1", "text": "环形缓冲 拼接 mp4"},
        {"project": "ems", "title": "功率控制", "url": "u2", "text": "mFRR 爬坡 告警"},
    ])
    assert ix.search("告警录像")[0]["url"] == "u1"
    assert [h["url"] for h in ix.search("告警", project="ems")] == ["u2"]
    assert ix.search("完全无关的词") == []


def test_domain_links_become_ip():
    from app.tools import public_links
    assert public_links("演示：https://vision.edgebot.vip/ 首页 https://edgebot.vip") == \
        "演示：http://47.117.29.74:8090/ 首页 http://47.117.29.74"
    assert public_links("https://qt.edgebot.vip/demo/topology/") == "http://47.117.29.74:8080/demo/topology/"


def test_rrf_fuses_keyword_and_vector_rankings():
    import numpy as np
    from app.search import Index

    class Fake:                     # "embedding": which topic the text is about
        model = "fake"
        def query(self, text):
            return np.array([1.0, 0.0]) if "并发" in text else np.array([0.0, 1.0])

    items = [{"project": "a", "title": "多路同时运行与性能", "url": "u1", "text": "每路一个进程"},
             {"project": "b", "title": "高并发", "url": "u2", "text": "高并发 高并发"},
             {"project": "c", "title": "界面", "url": "u3", "text": "按钮"}]
    ix = Index(items, np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]), Fake())
    hits = ix.search("怎么解决高并发")
    assert [h["url"] for h in hits] == ["u2", "u1"]             # u2 in both rankings, u1 only by meaning
    assert hits[0]["via"] == {"bm25": 1, "vec": 2} and hits[1]["via"] == {"vec": 1}
    assert ix.mode == "hybrid" and Index(items).mode == "bm25"


def test_excerpt_keeps_the_matching_part():
    from app.tools import excerpt
    text = "开头介绍\n" + "无关内容。\n" * 100 + "OpenVINO 绑核导致推理变慢，关掉绑核后 62 ms\n" + "结尾\n" * 50
    out = excerpt(text, "OpenVINO 绑核", 120)
    assert "OpenVINO 绑核导致推理变慢" in out and out.startswith("…") and len(out) <= 122
    assert excerpt("短文本", "x", 120) == "短文本"
