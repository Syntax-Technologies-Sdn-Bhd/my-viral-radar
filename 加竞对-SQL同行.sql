-- ============================================================
-- 把 competitors.json 里的 SQL 同行加进雷达:Instagram、Facebook、小红书三个平台。
-- 到 Supabase → SQL Editor → New query,整段贴上 → Run。重复跑不会报错。
-- 来源:Claude 大脑/reference/competitors.xlsx(2026-11-13 核对过)。
--
-- 想先省钱?只跑其中一段就好:每段只管一个平台。
-- 每周大概费用(Apify,每家抓最新 10 条):
--   Instagram 8 个账号 ≈ US$1–2/月
--   Facebook 11 个专页 ≈ US$3/月
--   小红书   9 个账号 ≈ US$3/月
-- ============================================================

-- ---------- Instagram ----------
-- GoLink、MyPoint、Emmanu 没找到 Instagram,所以不在这里。
insert into competitors (username, tracker, active) values
  ('sqlestream', 'IG', true),        -- eStream（SQL HQ）
  ('ams.software.sql', 'IG', true),  -- AMS
  ('sql_mysoftware', 'IG', true),    -- MySoftware
  ('esoftsql', 'IG', true),          -- eSoft
  ('wisoft.solutions', 'IG', true),  -- WiSoft
  ('sqlaccount_eist', 'IG', true),   -- EIST
  ('alpinesql', 'IG', true),         -- Alpine
  ('agile.my', 'IG', true)           -- Agile
on conflict do nothing;

-- ---------- Facebook(填完整专页链接)----------
insert into competitors (username, tracker, active) values
  ('https://www.facebook.com/SQLEstream/', 'FB', true),                -- eStream（SQL HQ）
  ('https://www.facebook.com/sql.ams.software/', 'FB', true),          -- AMS
  ('https://www.facebook.com/SQLMySoftware/', 'FB', true),             -- MySoftware
  ('https://www.facebook.com/eSoft.my/', 'FB', true),                  -- eSoft
  ('https://www.facebook.com/wisoftsolution', 'FB', true),             -- WiSoft
  ('https://www.facebook.com/sqlaccounteist/', 'FB', true),            -- EIST
  ('https://www.facebook.com/AlpineSQL', 'FB', true),                  -- Alpine
  ('https://www.facebook.com/golinksql', 'FB', true),                  -- GoLink
  ('https://www.facebook.com/SQLAccountingSoftwareGST/', 'FB', true),  -- MyPoint
  ('https://www.facebook.com/agile.my', 'FB', true),                   -- Agile
  ('https://www.facebook.com/el.com.my', 'FB', true)                   -- Emmanu
on conflict do nothing;

-- ---------- 小红书(填完整主页链接)----------
-- WiSoft、Agile 还没有小红书链接,所以不在这里。
insert into competitors (username, tracker, active) values
  ('https://www.xiaohongshu.com/user/profile/60ba509f0000000001008605', 'XHS', true),  -- eStream（SQL HQ）
  ('https://www.xiaohongshu.com/user/profile/67cf04ca000000000801644f', 'XHS', true),  -- AMS
  ('https://www.xiaohongshu.com/user/profile/62b95dc80000000019028283', 'XHS', true),  -- MySoftware
  ('https://www.xiaohongshu.com/user/profile/6302fe30000000000f004a8e', 'XHS', true),  -- eSoft
  ('https://www.xiaohongshu.com/user/profile/659b8ece000000001f012b31', 'XHS', true),  -- EIST
  ('https://www.xiaohongshu.com/user/profile/5e99d36e0000000001001c95', 'XHS', true),  -- Alpine
  ('https://www.xiaohongshu.com/user/profile/63759adc000000001f01ba10', 'XHS', true),  -- GoLink
  ('https://www.xiaohongshu.com/user/profile/65eadca600000000050080a3', 'XHS', true),  -- MyPoint
  ('https://www.xiaohongshu.com/user/profile/665202150000000003030818', 'XHS', true)   -- Emmanu
on conflict do nothing;

-- 如果 Instagram 那几个账号以前加过、但 active 没勾,这一句把它们重新打勾:
update competitors set active = true
where tracker = 'IG'
  and lower(username) in ('sqlestream','ams.software.sql','sql_mysoftware','esoftsql',
                          'wisoft.solutions','sqlaccount_eist','alpinesql','agile.my');

-- 想把自己(Syntax)也放进来对比,去掉下面几行开头的 -- 再跑:
-- insert into competitors (username, tracker, active) values
--   ('syntaxtechnologiessb', 'IG', true),
--   ('https://www.facebook.com/SyntaxTechnologies/', 'FB', true),
--   ('https://www.xiaohongshu.com/user/profile/63f1e2b5000000000f012765', 'XHS', true)
-- on conflict do nothing;

-- 想停掉某个平台(保留数据,只是不再抓、不再花钱):
-- update competitors set active = false where tracker = 'XHS';

-- 看现在在追踪哪些:
-- select username, tracker, active from competitors order by tracker, active desc, username;
