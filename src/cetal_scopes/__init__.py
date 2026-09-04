

def hello() -> str:
    return "Hello from cetal-scopes!"


def goodbye() -> str:
    return "Goodbye from cetal-scopes!"

def whatname() -> str:
    return __name__





if __name__ == "__main__":
    print(hello())
    print(goodbye())