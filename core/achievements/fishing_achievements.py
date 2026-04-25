from .base import BaseAchievement, UserContext


class TotalFishCount100(BaseAchievement):
    id = 2
    name = "小有所成"
    description = "累计钓到100条鱼"
    target_value = 100
    reward = ("coins", 500, 1)

    def get_progress(self, context: UserContext) -> int:
        """返回用户总钓鱼数作为当前进度。"""
        return context.user.total_fishing_count

    def check(self, context: UserContext) -> bool:
        # 使用 self.target_value 进行判断
        return context.user.total_fishing_count >= self.target_value

class FirstFishCaught(BaseAchievement):
    id = 1
    name = "新手钓手"
    description = "第一次钓鱼"
    target_value = 1
    reward = ("coins", 50, 1) # 奖励50个金币

    def get_progress(self, context: UserContext) -> int:
        """返回用户是否已经钓过鱼。"""
        return 1 if context.unique_fish_count > 0 else 0

    def check(self, context: UserContext) -> bool:
        return context.unique_fish_count > 0


class TenThousandFishCaught(BaseAchievement):
    id = 4
    name = "钓鱼大师"
    description = "累计钓上10000条鱼"
    target_value = 10000
    reward = ("coins", 10000, 1) # 奖励10000金币

    def get_progress(self, context: UserContext) -> int:
        """返回用户钓到的鱼总数作为当前进度。"""
        return context.user.total_fishing_count

    def check(self, context: UserContext) -> bool:
        return context.user.total_fishing_count >= 10000
class TotalFishCount1000(BaseAchievement):
    id = 3
    name = "百竿不空"
    description = "累计钓到1000条鱼"
    target_value = 1000
    reward = ("title", 3, 1)  # 奖励 "钓鱼大师" 称号

    def get_progress(self, context: UserContext) -> int:
        """返回用户总钓鱼数作为当前进度。"""
        return context.user.total_fishing_count

    def check(self, context: UserContext) -> bool:
        return context.user.total_fishing_count >= self.target_value
