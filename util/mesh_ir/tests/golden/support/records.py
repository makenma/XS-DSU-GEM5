from dataclasses import fields


def record(cls, **values):
    return cls(**({item.name: 0 for item in fields(cls)} | values))
