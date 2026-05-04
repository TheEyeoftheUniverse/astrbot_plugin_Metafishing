-- Latest bootstrap schema for fresh installs.

-- table: accessories
CREATE TABLE accessories (
            accessory_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT,
            rarity INTEGER NOT NULL DEFAULT 1, slot_type TEXT DEFAULT 'general' NOT NULL,
            bonus_fish_quality_modifier REAL DEFAULT 1.0, bonus_fish_quantity_modifier REAL DEFAULT 1.0,
            bonus_rare_fish_chance REAL DEFAULT 0.0, bonus_coin_modifier REAL DEFAULT 1.0,
            other_bonus_description TEXT, icon_url TEXT
        );

-- table: aquarium_upgrades
CREATE TABLE aquarium_upgrades (
            upgrade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            level INTEGER NOT NULL UNIQUE,
            capacity INTEGER NOT NULL,
            cost_coins INTEGER NOT NULL,
            cost_premium INTEGER DEFAULT 0,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

-- table: baits
CREATE TABLE baits (
            bait_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT,
            rarity INTEGER NOT NULL DEFAULT 1 CHECK (rarity >= 1), effect_description TEXT,
            duration_minutes INTEGER DEFAULT 0, cost INTEGER DEFAULT 0, required_rod_rarity INTEGER DEFAULT 0
        , success_rate_modifier REAL DEFAULT 0.0, rare_chance_modifier REAL DEFAULT 0.0, garbage_reduction_modifier REAL DEFAULT 0.0, value_modifier REAL DEFAULT 1.0, quantity_modifier REAL DEFAULT 1.0, is_consumable INTEGER DEFAULT 1);

-- table: check_ins
CREATE TABLE check_ins (
            user_id TEXT NOT NULL, check_in_date DATE NOT NULL,
            PRIMARY KEY (user_id, check_in_date),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: commodities
CREATE TABLE commodities (
            commodity_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT
        );


-- table: exchange_commodity_rules
CREATE TABLE exchange_commodity_rules (
            commodity_id TEXT PRIMARY KEY,
            volatility REAL NOT NULL CHECK (volatility >= 0),
            max_change_rate REAL NOT NULL CHECK (max_change_rate >= 0),
            min_price INTEGER NOT NULL DEFAULT 1 CHECK (min_price > 0),
            max_price INTEGER NOT NULL DEFAULT 1000000 CHECK (max_price >= min_price),
            FOREIGN KEY (commodity_id) REFERENCES commodities(commodity_id) ON DELETE CASCADE
        );

-- table: exchange_prices
CREATE TABLE exchange_prices (
            price_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            commodity_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            update_type TEXT DEFAULT 'auto',
            created_at TEXT NOT NULL,
            FOREIGN KEY (commodity_id) REFERENCES commodities(commodity_id)
        );

-- table: exchange_market_snapshots
CREATE TABLE exchange_market_snapshots (
            date TEXT PRIMARY KEY,
            supply_demand TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

-- table: fish
CREATE TABLE "fish" (
            fish_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            rarity INTEGER NOT NULL CHECK (rarity >= 1),
            base_value INTEGER NOT NULL,
            icon_url TEXT
        );

-- table: fishing_zones
CREATE TABLE fishing_zones (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            daily_rare_fish_quota INTEGER NOT NULL,
            rare_fish_caught_today INTEGER NOT NULL DEFAULT 0
        , configs TEXT, is_active INTEGER DEFAULT 1, available_from TEXT, available_until TEXT, required_item_id INTEGER, requires_pass BOOLEAN DEFAULT 0, fishing_cost INTEGER DEFAULT 10);

-- table: gacha_pool_items
CREATE TABLE gacha_pool_items (
                gacha_pool_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                gacha_pool_id INTEGER NOT NULL,
                item_type TEXT NOT NULL CHECK (
                    item_type IN ('rod', 'accessory', 'bait', 'fish', 'coins', 'premium_currency', 'item')
                ),
                item_id INTEGER NOT NULL,
                quantity INTEGER DEFAULT 1,
                weight INTEGER NOT NULL CHECK (weight > 0),
                FOREIGN KEY (gacha_pool_id) REFERENCES gacha_pools(gacha_pool_id) ON DELETE CASCADE
            );

-- table: gacha_pools
CREATE TABLE gacha_pools (
            gacha_pool_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT,
            cost_coins INTEGER DEFAULT 0, cost_premium_currency INTEGER DEFAULT 0
        , is_limited_time INTEGER DEFAULT 0, open_until TEXT);

-- table: items
CREATE TABLE items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            rarity INTEGER NOT NULL DEFAULT 1,
            effect_description TEXT,
            cost INTEGER DEFAULT 0,
            is_consumable INTEGER DEFAULT 1,
            icon_url TEXT
        , effect_type TEXT, effect_payload TEXT);

-- table: market
CREATE TABLE "market" (
                market_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                price INTEGER NOT NULL,
                listed_at TEXT NOT NULL,
                expires_at TEXT,
                refine_level INTEGER DEFAULT 1,
                seller_nickname TEXT,
                item_name TEXT,
                item_description TEXT,
                item_instance_id INTEGER,
                is_anonymous INTEGER DEFAULT 0,
                quality_level INTEGER DEFAULT 0,
                UNIQUE(user_id, item_type, item_id, quality_level, item_instance_id)
            );

-- table: rods
CREATE TABLE rods (
            rod_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT,
            rarity INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL CHECK (source IN ('shop', 'gacha', 'event')),
            purchase_cost INTEGER, bonus_fish_quality_modifier REAL DEFAULT 1.0,
            bonus_fish_quantity_modifier REAL DEFAULT 1.0, bonus_rare_fish_chance REAL DEFAULT 0.0,
            durability INTEGER, icon_url TEXT
        );

-- table: shop_item_costs
CREATE TABLE "shop_item_costs" (
            cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            cost_type TEXT NOT NULL CHECK (cost_type IN ('coins','premium','item','fish','rod','accessory','bait')),
            cost_amount INTEGER NOT NULL CHECK (cost_amount > 0),
            cost_item_id INTEGER,  -- cost_type 为具体物品时使用
            cost_relation TEXT DEFAULT 'and' CHECK (cost_relation IN ('and', 'or')),
            group_id INTEGER, quality_level INTEGER DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE
        );

-- table: shop_item_rewards
CREATE TABLE shop_item_rewards (
            reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            reward_type TEXT NOT NULL CHECK (reward_type IN ('rod','accessory','bait','item','fish','coins')),
            reward_item_id INTEGER,  -- reward_type 为具体物品时使用
            reward_quantity INTEGER NOT NULL DEFAULT 1 CHECK (reward_quantity > 0),
            reward_refine_level INTEGER, quality_level INTEGER DEFAULT 0,  -- 精炼等级
            FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE
        );

-- table: shop_items
CREATE TABLE shop_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            category TEXT DEFAULT 'general' NOT NULL,
            
            -- 库存和限购信息
            stock_total INTEGER,  -- NULL 表示无限库存
            stock_sold INTEGER DEFAULT 0 NOT NULL,
            per_user_limit INTEGER,  -- NULL 表示无限
            per_user_daily_limit INTEGER,  -- NULL 表示无限
            
            -- 状态和时间
            is_active BOOLEAN DEFAULT TRUE NOT NULL,
            start_time DATETIME,
            end_time DATETIME,
            sort_order INTEGER DEFAULT 100 NOT NULL,
            
            -- 时间戳
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME,
            
            FOREIGN KEY (shop_id) REFERENCES shops(shop_id) ON DELETE CASCADE
        );

-- table: shop_purchase_records
CREATE TABLE shop_purchase_records (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: shops
CREATE TABLE shops (
            shop_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            shop_type TEXT NOT NULL DEFAULT 'normal' CHECK (shop_type IN ('normal','premium','limited')),
            is_active BOOLEAN DEFAULT TRUE NOT NULL,
            start_time DATETIME,  -- 开始日期时间
            end_time DATETIME,    -- 结束日期时间
            daily_start_time TIME,  -- 每日开始时间（如 09:00）
            daily_end_time TIME,    -- 每日结束时间（如 18:00）
            sort_order INTEGER DEFAULT 100 NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME
        );

-- table: taxes
CREATE TABLE taxes (
            tax_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            tax_amount INTEGER NOT NULL, tax_rate REAL NOT NULL, original_amount INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, tax_type TEXT NOT NULL,
            balance_after INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: titles
CREATE TABLE titles (
            title_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
            display_format TEXT DEFAULT '{name}'
        );

-- table: user_accessories
CREATE TABLE user_accessories (
            accessory_instance_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            accessory_id INTEGER NOT NULL, obtained_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_equipped INTEGER DEFAULT 0 CHECK (is_equipped IN (0, 1)), refine_level INTEGER DEFAULT 1, is_locked INTEGER DEFAULT 0 CHECK (is_locked IN (0, 1)), display_code TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (accessory_id) REFERENCES accessories(accessory_id) ON DELETE CASCADE
        );

-- table: user_achievement_progress
CREATE TABLE user_achievement_progress (
            user_id TEXT NOT NULL,
            achievement_id INTEGER NOT NULL,
            current_progress INTEGER DEFAULT 0,
            completed_at DATETIME,
            claimed_at DATETIME,
            PRIMARY KEY (user_id, achievement_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: user_aquarium
CREATE TABLE user_aquarium (
            user_id TEXT NOT NULL,
            fish_id INTEGER NOT NULL,
            quality_level INTEGER DEFAULT 0 CHECK (quality_level IN (0, 1)),
            quantity INTEGER DEFAULT 0 CHECK (quantity >= 0),
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, fish_id, quality_level),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE CASCADE
        );

-- table: user_bait_inventory
CREATE TABLE user_bait_inventory (
            user_id TEXT NOT NULL, bait_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 0 CHECK (quantity >= 0),
            PRIMARY KEY (user_id, bait_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (bait_id) REFERENCES baits(bait_id) ON DELETE CASCADE
        );

-- table: user_buffs
CREATE TABLE user_buffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            buff_type TEXT NOT NULL,
            payload TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: user_commodities
CREATE TABLE user_commodities (
            instance_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            commodity_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            purchase_price INTEGER NOT NULL,
            purchased_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (commodity_id) REFERENCES commodities(commodity_id) ON DELETE CASCADE
        );

-- table: user_fish_inventory
CREATE TABLE user_fish_inventory (
            user_id TEXT NOT NULL,
            fish_id INTEGER NOT NULL,
            quality_level INTEGER DEFAULT 0 CHECK (quality_level IN (0, 1)),
            quantity INTEGER DEFAULT 0 CHECK (quantity >= 0),
            no_sell_until DATETIME,
            PRIMARY KEY (user_id, fish_id, quality_level),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE CASCADE
        );

-- table: user_fish_stats
CREATE TABLE user_fish_stats (
            user_id TEXT NOT NULL,
            fish_id INTEGER NOT NULL,
            first_caught_at DATETIME,
            last_caught_at DATETIME,
            total_caught INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, fish_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE RESTRICT
        );

-- table: user_equipment_stats
CREATE TABLE user_equipment_stats (
            user_id TEXT NOT NULL,
            equipment_type TEXT NOT NULL CHECK (equipment_type IN ('rod', 'accessory', 'bait')),
            equipment_id INTEGER NOT NULL,
            first_obtained_at DATETIME,
            last_obtained_at DATETIME,
            total_obtained INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, equipment_type, equipment_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: user_pokedex_reward_claims
CREATE TABLE user_pokedex_reward_claims (
            user_id TEXT NOT NULL,
            milestone_percent INTEGER NOT NULL,
            reward_premium INTEGER NOT NULL,
            reward_type TEXT NOT NULL DEFAULT 'premium' CHECK (reward_type IN ('coins', 'premium')),
            reward_amount INTEGER NOT NULL DEFAULT 0,
            claimed_unlocked_fish_count INTEGER NOT NULL,
            claimed_total_fish_count INTEGER NOT NULL,
            claimed_at DATETIME NOT NULL,
            PRIMARY KEY (user_id, milestone_percent),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: user_equipment_pokedex_reward_claims
CREATE TABLE user_equipment_pokedex_reward_claims (
            user_id TEXT NOT NULL,
            milestone_percent INTEGER NOT NULL,
            reward_type TEXT NOT NULL CHECK (reward_type IN ('coins', 'premium')),
            reward_amount INTEGER NOT NULL,
            claimed_unlocked_equipment_count INTEGER NOT NULL,
            claimed_total_equipment_count INTEGER NOT NULL,
            claimed_at DATETIME NOT NULL,
            PRIMARY KEY (user_id, milestone_percent),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

-- table: user_items
CREATE TABLE user_items (
            user_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 0 CHECK (quantity >= 0),
            PRIMARY KEY (user_id, item_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
        );

-- table: user_rods
CREATE TABLE user_rods (
            rod_instance_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            rod_id INTEGER NOT NULL, obtained_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            current_durability INTEGER, is_equipped INTEGER DEFAULT 0 CHECK (is_equipped IN (0, 1)), refine_level INTEGER DEFAULT 1, is_locked INTEGER DEFAULT 0 CHECK (is_locked IN (0, 1)), display_code TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (rod_id) REFERENCES rods(rod_id) ON DELETE CASCADE
        );

-- table: user_titles
CREATE TABLE user_titles (
            user_id TEXT NOT NULL, title_id INTEGER NOT NULL,
            unlocked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, title_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (title_id) REFERENCES titles(title_id) ON DELETE CASCADE
        );

-- table: user_zone_stays
CREATE TABLE user_zone_stays (
            user_id TEXT NOT NULL,
            zone_id INTEGER NOT NULL,
            pass_item_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, zone_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (zone_id) REFERENCES fishing_zones(id) ON DELETE CASCADE
        );

-- table: users
CREATE TABLE users (
            user_id TEXT PRIMARY KEY, nickname TEXT, coins INTEGER DEFAULT 200,
            premium_currency INTEGER DEFAULT 0, total_fishing_count INTEGER DEFAULT 0,
            total_coins_earned INTEGER DEFAULT 0,
            equipped_rod_instance_id INTEGER, equipped_accessory_instance_id INTEGER,
            current_bait_id INTEGER, bait_start_time DATETIME, current_title_id INTEGER,
            auto_fishing_enabled INTEGER DEFAULT 0, last_fishing_time DATETIME,
            last_wipe_bomb_time DATETIME, last_steal_time DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP, last_login_time DATETIME,
            consecutive_login_days INTEGER DEFAULT 0, fish_pond_capacity INTEGER DEFAULT 480,
            last_stolen_at DATETIME
        , fishing_zone_id INTEGER DEFAULT 1, wipe_bomb_forecast TEXT, aquarium_capacity INTEGER DEFAULT 50, exchange_account_status INTEGER DEFAULT 0, last_electric_fish_time DATETIME, max_wipe_bomb_multiplier REAL DEFAULT 0.0, min_wipe_bomb_multiplier REAL DEFAULT NULL, wipe_bomb_attempts_today INTEGER NOT NULL DEFAULT 0, last_wipe_bomb_date TEXT DEFAULT NULL, max_coins INTEGER DEFAULT 0, exchange_capacity INTEGER DEFAULT 1000);

-- table: zone_fish_mapping
CREATE TABLE zone_fish_mapping (
            zone_id INTEGER,
            fish_id INTEGER,
            PRIMARY KEY (zone_id, fish_id),
            FOREIGN KEY (zone_id) REFERENCES fishing_zones(id) ON DELETE CASCADE,
            FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE CASCADE
        );

-- index: idx_aquarium_upgrades_level
CREATE INDEX idx_aquarium_upgrades_level ON aquarium_upgrades(level);

-- index: idx_check_ins_date
CREATE INDEX idx_check_ins_date ON check_ins(check_in_date);

-- index: idx_exchange_commodity_rules_commodity
CREATE INDEX idx_exchange_commodity_rules_commodity ON exchange_commodity_rules(commodity_id);

-- index: idx_exchange_prices_created_at
CREATE INDEX idx_exchange_prices_created_at ON exchange_prices(created_at);

-- index: idx_exchange_prices_date_commodity
CREATE INDEX idx_exchange_prices_date_commodity ON exchange_prices(date, commodity_id);

-- index: idx_gacha_pool_items_pool_type
CREATE INDEX idx_gacha_pool_items_pool_type ON gacha_pool_items(gacha_pool_id, item_type);

-- index: idx_market_item_quality
CREATE INDEX idx_market_item_quality 
            ON market(item_type, item_id, quality_level)
        ;

-- index: idx_market_user_item_quality
CREATE INDEX idx_market_user_item_quality 
            ON market(user_id, item_type, item_id, quality_level)
        ;

-- index: idx_shop_item_costs_fish_quality
CREATE INDEX idx_shop_item_costs_fish_quality 
            ON shop_item_costs(cost_type, cost_item_id, quality_level) 
            WHERE cost_type = 'fish'
        ;

-- index: idx_shop_item_costs_group
CREATE INDEX idx_shop_item_costs_group ON shop_item_costs(group_id);

-- index: idx_shop_item_costs_item
CREATE INDEX idx_shop_item_costs_item ON shop_item_costs(item_id);

-- index: idx_shop_item_costs_relation
CREATE INDEX idx_shop_item_costs_relation ON shop_item_costs(cost_relation);

-- index: idx_shop_item_costs_type
CREATE INDEX idx_shop_item_costs_type ON shop_item_costs(cost_type);

-- index: idx_shop_item_rewards_item
CREATE INDEX idx_shop_item_rewards_item ON shop_item_rewards(item_id);

-- index: idx_shop_item_rewards_type
CREATE INDEX idx_shop_item_rewards_type ON shop_item_rewards(reward_type);

-- index: idx_shop_items_active_time
CREATE INDEX idx_shop_items_active_time ON shop_items(is_active, start_time, end_time);

-- index: idx_shop_items_category
CREATE INDEX idx_shop_items_category ON shop_items(category);

-- index: idx_shop_items_shop
CREATE INDEX idx_shop_items_shop ON shop_items(shop_id);

-- index: idx_shop_purchase_time
CREATE INDEX idx_shop_purchase_time ON shop_purchase_records(timestamp);

-- index: idx_shop_purchase_user_item
CREATE INDEX idx_shop_purchase_user_item ON shop_purchase_records(user_id, item_id);

-- index: idx_shops_active_time
CREATE INDEX idx_shops_active_time ON shops(is_active, start_time, end_time);

-- index: idx_shops_daily_time
CREATE INDEX idx_shops_daily_time ON shops(daily_start_time, daily_end_time);

-- index: idx_shops_type
CREATE INDEX idx_shops_type ON shops(shop_type);

-- index: idx_user_accessories_display_code
CREATE UNIQUE INDEX idx_user_accessories_display_code ON user_accessories(display_code);

-- index: idx_user_accessories_locked
CREATE INDEX idx_user_accessories_locked ON user_accessories(user_id, is_locked);

-- index: idx_user_accessories_user
CREATE INDEX idx_user_accessories_user ON user_accessories(user_id);

-- index: idx_user_bait_inventory_user
CREATE INDEX idx_user_bait_inventory_user ON user_bait_inventory(user_id);

-- index: idx_user_buffs_expires_at
CREATE INDEX idx_user_buffs_expires_at ON user_buffs(expires_at);

-- index: idx_user_buffs_user_id
CREATE INDEX idx_user_buffs_user_id ON user_buffs(user_id);

-- index: idx_user_commodities_commodity
CREATE INDEX idx_user_commodities_commodity ON user_commodities(commodity_id);

-- index: idx_user_commodities_expires
CREATE INDEX idx_user_commodities_expires ON user_commodities(expires_at);

-- index: idx_user_commodities_user
CREATE INDEX idx_user_commodities_user ON user_commodities(user_id);

-- index: idx_user_fish_stats_fish
CREATE INDEX idx_user_fish_stats_fish ON user_fish_stats(fish_id);

-- index: idx_user_fish_stats_user
CREATE INDEX idx_user_fish_stats_user ON user_fish_stats(user_id);

-- index: idx_user_equipment_stats_equipment
CREATE INDEX idx_user_equipment_stats_equipment ON user_equipment_stats(equipment_type, equipment_id);

-- index: idx_user_equipment_stats_user
CREATE INDEX idx_user_equipment_stats_user ON user_equipment_stats(user_id);

-- index: idx_user_rods_display_code
CREATE UNIQUE INDEX idx_user_rods_display_code ON user_rods(display_code);

-- index: idx_user_rods_locked
CREATE INDEX idx_user_rods_locked ON user_rods(user_id, is_locked);

-- index: idx_user_rods_user
CREATE INDEX idx_user_rods_user ON user_rods(user_id);

-- index: idx_user_titles_user
CREATE INDEX idx_user_titles_user ON user_titles(user_id);

-- index: idx_user_zone_stays_expires_at
CREATE INDEX idx_user_zone_stays_expires_at ON user_zone_stays(expires_at);

-- index: idx_users_auto_fishing_enabled
CREATE INDEX idx_users_auto_fishing_enabled ON users(auto_fishing_enabled) WHERE auto_fishing_enabled = 1;

-- index: idx_users_coins
CREATE INDEX idx_users_coins ON users(coins);

-- index: idx_users_last_login
CREATE INDEX idx_users_last_login ON users(last_login_time);
