from hy.macros import macro
from hy import HyList, HyInteger


SECRET_MESSAGE = "Hello World"


@macro("qplah")
def qplah(ETname, *tree):
    return HyList((HyInteger(8), ) + tree)

@macro("parald")
def parald(ETname, *tree):
    return HyList((HyInteger(9), ) + tree)
