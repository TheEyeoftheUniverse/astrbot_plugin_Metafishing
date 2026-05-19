def up(cursor):
    cursor.executescript(
        """
        UPDATE user_cthulhu_state
        SET current_san = 50,
            max_san = 50
        WHERE current_san = 100 AND max_san = 100;
        """
    )


def down(cursor):
    cursor.executescript(
        """
        UPDATE user_cthulhu_state
        SET current_san = 100,
            max_san = 100
        WHERE current_san = 50 AND max_san = 50;
        """
    )
