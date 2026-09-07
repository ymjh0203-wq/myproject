from collector.taobao_candidate_search import (
    extract_item_id,
    merge_and_rank,
    parse_number,
    parse_price,
    suggest_chinese_query,
)


def test_item_id_from_taobao_and_tmall_urls():
    assert extract_item_id("https://item.taobao.com/item.htm?id=12345678901") == "12345678901"
    assert extract_item_id("https://detail.tmall.com/item.htm?itemId=9988776655") == "9988776655"


def test_chinese_and_korean_quantity_units():
    assert parse_number("1.2万人付款") == 12000
    assert parse_number("3千+ 已售") == 3000
    assert parse_number("456人付款") == 456


def test_price_parsing():
    assert parse_price("到手价 ￥39.90") == 39.9
    assert parse_price("¥ 11 .12 券后价") == 11.12


def test_query_suggestion_is_editable_seed():
    assert suggest_chinese_query("관리기", ["밭갈이", "무료배송"]) == "微耕机 旋耕机"


def test_merge_deduplicates_and_rewards_cross_route():
    keyword = [{"url": "https://item.taobao.com/item.htm?id=12345678901", "title": "A",
                "image_url": "https://img/a.jpg", "price_cny": 10.0, "sales_count": 100}]
    image = [{"url": "https://detail.tmall.com/item.htm?id=12345678901", "title": "A",
              "image_url": "https://img/a.jpg", "price_cny": 10.0, "sales_count": 100},
             {"url": "https://item.taobao.com/item.htm?id=22345678901", "title": "B",
              "image_url": "https://img/b.jpg", "price_cny": 20.0, "sales_count": 10000}]
    ranked = merge_and_rank([("키워드", keyword), ("이미지", image)], 3)
    assert len(ranked) == 2
    shared = next(item for item in ranked if item["item_id"] == "12345678901")
    assert shared["routes"] == ["키워드", "이미지"]
    assert all(item["rank"] >= 1 for item in ranked)
