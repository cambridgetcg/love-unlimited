"""兩盞燈共用嘅窄 IO primitives；唔承載產品 schema 或安裝策略。"""


class CommonError(Exception):
    """只傳固定 code；唔帶 raw exception 或檔案內容。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
