-- Supplemental static seed that is not sourced from fish_db.xlsx.

INSERT INTO "items" ("item_id", "name", "description", "rarity", "effect_description", "cost", "is_consumable", "icon_url", "effect_type", "effect_payload")
VALUES (55, '深潜门票', '进入区域七时会被克苏鲁深潜系统自动消耗，用于开启当日深潜。', 6, '进入区域七开始钓鱼时自动消耗，开启一次当日深潜。', 0, 1, NULL, 'NONE', '{}')
ON CONFLICT("item_id") DO UPDATE SET
    "name" = excluded."name",
    "description" = excluded."description",
    "rarity" = excluded."rarity",
    "effect_description" = excluded."effect_description",
    "cost" = excluded."cost",
    "is_consumable" = excluded."is_consumable",
    "icon_url" = excluded."icon_url",
    "effect_type" = excluded."effect_type",
    "effect_payload" = excluded."effect_payload";

INSERT INTO "items" ("item_id", "name", "description", "rarity", "effect_description", "cost", "is_consumable", "icon_url", "effect_type", "effect_payload")
VALUES (56, '低语之露', '冰凉如夜潮的露滴，入口后理智会被勉强缝回一点。', 6, '使用后恢复 5 点 SAN，不会超过当前上限。', 0, 1, NULL, 'ADD_SAN', '{"amount": 5}')
ON CONFLICT("item_id") DO UPDATE SET
    "name" = excluded."name",
    "description" = excluded."description",
    "rarity" = excluded."rarity",
    "effect_description" = excluded."effect_description",
    "cost" = excluded."cost",
    "is_consumable" = excluded."is_consumable",
    "icon_url" = excluded."icon_url",
    "effect_type" = excluded."effect_type",
    "effect_payload" = excluded."effect_payload";

INSERT INTO "items" ("item_id", "name", "description", "rarity", "effect_description", "cost", "is_consumable", "icon_url", "effect_type", "effect_payload")
VALUES (57, '古旧瞳孔', '像鱼眼又像星眼的干瘪器官，被它看过的人会多承受一点真相。', 8, '使用后永久提升 1 点 SAN 上限。', 0, 1, NULL, 'ADD_MAX_SAN', '{"amount": 1}')
ON CONFLICT("item_id") DO UPDATE SET
    "name" = excluded."name",
    "description" = excluded."description",
    "rarity" = excluded."rarity",
    "effect_description" = excluded."effect_description",
    "cost" = excluded."cost",
    "is_consumable" = excluded."is_consumable",
    "icon_url" = excluded."icon_url",
    "effect_type" = excluded."effect_type",
    "effect_payload" = excluded."effect_payload";

INSERT INTO "items" ("item_id", "name", "description", "rarity", "effect_description", "cost", "is_consumable", "icon_url", "effect_type", "effect_payload")
VALUES (58, '协议重写芯', '重写当前觉醒协议的稀缺芯片。使用后仅清空觉醒协议，不退还科研点与分支等级。', 8, '用于 /重写协议 或科幻页面重置当前觉醒协议。', 0, 1, NULL, 'NONE', '{}')
ON CONFLICT("item_id") DO UPDATE SET
    "name" = excluded."name",
    "description" = excluded."description",
    "rarity" = excluded."rarity",
    "effect_description" = excluded."effect_description",
    "cost" = excluded."cost",
    "is_consumable" = excluded."is_consumable",
    "icon_url" = excluded."icon_url",
    "effect_type" = excluded."effect_type",
    "effect_payload" = excluded."effect_payload";

INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('predict_upper', 'predict', 'upper', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('predict_middle', 'predict', 'middle', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('predict_lower', 'predict', 'lower', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('time_upper', 'time', 'upper', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('time_middle', 'time', 'middle', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('time_lower', 'time', 'lower', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('pollute_upper', 'pollute', 'upper', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('pollute_middle', 'pollute', 'middle', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('pollute_lower', 'pollute', 'lower', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('sacrifice_upper', 'sacrifice', 'upper', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('sacrifice_middle', 'sacrifice', 'middle', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;
INSERT INTO "cthulhu_authority" ("authority_id", "god_type", "tier", "current_holder", "acquired_at", "previous_holder", "previous_acquired_at") VALUES ('sacrifice_lower', 'sacrifice', 'lower', NULL, NULL, NULL, NULL) ON CONFLICT("authority_id") DO NOTHING;

INSERT INTO "cthulhu_global_pollution" ("pollution_id", "activated_at", "triggered_by_name_id") VALUES ('U1', NULL, NULL) ON CONFLICT("pollution_id") DO NOTHING;
INSERT INTO "cthulhu_global_pollution" ("pollution_id", "activated_at", "triggered_by_name_id") VALUES ('U2', NULL, NULL) ON CONFLICT("pollution_id") DO NOTHING;
INSERT INTO "cthulhu_global_pollution" ("pollution_id", "activated_at", "triggered_by_name_id") VALUES ('U5', NULL, NULL) ON CONFLICT("pollution_id") DO NOTHING;
INSERT INTO "cthulhu_global_pollution" ("pollution_id", "activated_at", "triggered_by_name_id") VALUES ('U8', NULL, NULL) ON CONFLICT("pollution_id") DO NOTHING;
INSERT INTO "cthulhu_global_pollution" ("pollution_id", "activated_at", "triggered_by_name_id") VALUES ('U10', NULL, NULL) ON CONFLICT("pollution_id") DO NOTHING;
INSERT INTO "cthulhu_global_pollution" ("pollution_id", "activated_at", "triggered_by_name_id") VALUES ('U11', NULL, NULL) ON CONFLICT("pollution_id") DO NOTHING;
INSERT INTO "cthulhu_global_pollution" ("pollution_id", "activated_at", "triggered_by_name_id") VALUES ('U14', NULL, NULL) ON CONFLICT("pollution_id") DO NOTHING;
