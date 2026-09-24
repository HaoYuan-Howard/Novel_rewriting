"""提前注册选测选项，避免 --changed 的文档路径被 pytest 误判为测试路径。"""

pytest_plugins = ['tests.selection']
