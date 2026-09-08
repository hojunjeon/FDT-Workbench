"""Explicit, reviewable mapping from raw CSV labels to KeyFin budget envelopes.

Raw ``category``/``subcategory`` are authoritative observations and are retained on
all transactions.  Mapping to KeyFin's seven envelopes is a projection used only
for budget/simulation controls.  Exact subcategory mappings win; the raw category
is used only as a safe fallback so an unknown subcategory is not silently collapsed
into ``기타``.
"""
MAPPING_VERSION = 'keyfin-csv-map/1.1'
ENVELOPES = ('외식', '교통비', '의료·건강', '취미·여가', '쇼핑', '편의점·마트·잡화', '기타')
GROUPS = {
    '외식': {'음식점': ['점심','저녁/외식','가족 외식','고객 미팅','빵·간식','간식','반찬','모임'],
           '카페':['카페'], '배달':['배달'], '주점':['주점']},
    '교통비': {'대중교통':['대중교통','기차'], '택시':['택시','대리운전','주차'], '주유':['주유']},
    '의료·건강': {'병원·약국':['병원','약국','한의원','안경','건강식품'], '운동·헬스':['헬스장','수영']},
    '취미·여가': {'영화·공연·전시':['영화/공연','전시','관람'], '스포츠 관람':['스포츠 관람'],
               '게임·콘텐츠':['게임','노래방'], '여행·숙박':['숙박','놀이공원','키즈카페','문화센터']},
    '쇼핑': {'패션·잡화':['의류','선물'], '뷰티':['미용','뷰티·건강'], '온라인 쇼핑':['장난감']},
    '편의점·마트·잡화': {'편의점':['편의점'], '마트':['장보기'], '생활용품':['생활용품','학용품']},
    '기타': {'교육':['도서','온라인 강의','스터디카페','시험 응시료','등록금','자녀 캠프','자녀 학원'],
           '해외 결제':['해외 결제'],
           '경조사·기타':['가스요금','전기요금','구독','월세','인터넷','통신','경조사','회비','가족 생활비',
                     '가족 용돈','관리비','사회보험','연금','자동차세','코워킹','종교','기부','실손보험','세차']}
}
LOOKUP = {raw:(env,sub) for env, groups in GROUPS.items() for sub, raws in groups.items() for raw in raws}

# Fallback is intentionally envelope-only.  We retain the raw subcategory instead
# of inventing one of the 22 KeyFin subcategories when the exact mapping is unknown.
CATEGORY_ENVELOPE = {
    '식비': '외식',
    '교통': '교통비',
    '건강': '의료·건강',
    '여가·문화': '취미·여가',
    '쇼핑': '쇼핑',
    '생활서비스': '쇼핑',
    '교육': '기타',
    '사회·경조': '기타',
    '주거·통신': '기타',
    '세금·공과': '기타',
    '보험': '기타',
    '업무': '기타',
}


def classify(category: str, subcategory: str) -> tuple[str, str, str]:
    """Return ``(envelope, mapped_subcategory, source)``.

    ``source`` is ``subcategory_exact`` or ``category_fallback``.  Unknown raw
    categories remain visible as ``기타`` with the original subcategory preserved.
    """
    if subcategory in LOOKUP:
        env, mapped = LOOKUP[subcategory]
        return env, mapped, 'subcategory_exact'
    env = CATEGORY_ENVELOPE.get(category, '기타')
    return env, subcategory or '경조사·기타', 'category_fallback'
