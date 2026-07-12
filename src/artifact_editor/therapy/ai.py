from gpu import llm as gpu_llm
from . import htmx



def process(prompt, outputfile):
    """
    The user just send us a message.  The new purpose of our existence is to
    answer that message in the most thoughtful and considerate manner possible.
    """
    # llm = gpu_llm.Qwen3_8B_Q4_K_M()
    llm = gpu_llm.Qwen3_5_9B_Q5_K_M()

    message = llm.str_tools_prompt(
        prompt,
        system_prompt="You are a blunt and cold AI designed to do exactly what you are told with precision.  Your user is an expert, you can be very technical.",
    )

    with open(outputfile, 'w') as f:
        f.write(message)

    return