-- ============================================================
-- 把 competitors.json 里所有有 Instagram 的 SQL 同行加进雷达。
-- 到 Supabase → SQL Editor → New query,整段贴上 → Run。重复跑不会报错。
-- 来源:Claude 大脑/reference/competitors.xlsx(2026-11-13 核对过)。
-- GoLink、MyPoint、Emmanu 没找到 Instagram,所以不在这里。
-- ============================================================
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

-- 如果这些账号以前加过、但 active 没勾,这一句把它们重新打勾:
update competitors set active = true
where lower(username) in ('sqlestream','ams.software.sql','sql_mysoftware','esoftsql',
                          'wisoft.solutions','sqlaccount_eist','alpinesql','agile.my');

-- 想把自己(Syntax)也放进来对比,去掉下面这行开头的 -- 再跑:
-- insert into competitors (username, tracker, active) values ('syntaxtechnologiessb', 'IG', true) on conflict do nothing;

-- 看现在在追踪哪些:
-- select username, tracker, active from competitors order by active desc, username;
