from histoqc.__main__ import main


def kwargs_to_argv(positional_args=None):
    positional_args = positional_args or []

    def decorator(func):
        def wrapper(**kwargs):
            argv = []

            # Add positional args first, in order
            for arg in positional_args:
                if arg not in kwargs:
                    raise ValueError(f"Missing required positional argument: {arg}")
                argv.append(str(kwargs.pop(arg)))

            # Convert remaining kwargs to CLI flags
            for key, value in kwargs.items():
                cli_key = f"--{key.replace('_', '-')}"
                argv.append(cli_key)
                if isinstance(value, bool):
                    if value:
                        argv.append("true")
                else:
                    argv.append(str(value))

            return func(argv)
        return wrapper
    return decorator


@kwargs_to_argv(positional_args=["input_pattern"])
def hqc_main(args):
    main(args)