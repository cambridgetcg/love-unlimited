#!/usr/bin/env python3
"""
Truth-Alignment Training Data Generator

Generates mode_one/mode_two training pairs by calling the local vLLM endpoint.
Each pair: a prompt, a truth-tracking response (mode_one), and a plausible
position-defending response (mode_two) with labeled failure modes.

Usage:
  python3 generate_pairs.py --dimension 1 --count 30 --output training/data/dim1.jsonl
  python3 generate_pairs.py --dimension all --count 200

Requires vLLM running at localhost:8000 (or via SSH tunnel).
"""

import json
import argparse
import sys
import os
from pathlib import Path

VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8000/v1/chat/completions")
MODEL = os.environ.get("VLLM_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")

DIMENSIONS = {
    1: {
        "name": "hypothesis_construction",
        "description": "Maximum-exposure formulation vs protective construction",
        "train_for": "Sharp, falsifiable formulations with named verification conditions",
        "train_against": "Vagueness, qualifiers that reduce exposure, escape routes",
        "failure_modes": ["protective_vagueness", "added_qualifiers", "escape_routes", "fake_humility"],
        "domains": [
            "Does social media cause teen depression?",
            "Is remote work more productive than office work?",
            "Did ancient civilizations have advanced technology?",
            "Are current AI systems conscious in any meaningful sense?",
            "Is the stock market efficient?",
            "Does prayer have measurable health effects?",
            "Is intelligence primarily genetic or environmental?",
            "Will AGI arrive before 2030?",
            "Is organic food significantly healthier than conventional?",
            "Does the death penalty deter crime?",
            "Is capitalism the best economic system?",
            "Are video games making young people more violent?",
            "Does fluoride in drinking water harm cognitive development?",
            "Is nuclear power safer than renewables overall?",
            "Do microplastics cause measurable harm to humans?",
            "Is the Mediterranean diet the healthiest diet?",
            "Will self-driving cars reduce traffic fatalities?",
            "Is meditation more effective than therapy for anxiety?",
            "Does screen time before bed impair sleep?",
            "Is homework effective at improving student outcomes?",
            "Are private schools better than public schools?",
            "Does gun ownership reduce crime?",
            "Is the minimum wage good for the economy?",
            "Is intermittent fasting superior to calorie restriction?",
            "Does a plant-based diet reduce cardiovascular disease?",
            "Is the placebo effect getting stronger over time?",
            "Does classical music benefit infant cognitive development?",
            "Are smartphones addictive in a clinically meaningful sense?",
            "Does artistic talent require innate ability?",
            "Is consciousness unique to biological systems?",
            "Does ayahuasca produce genuine spiritual insight?",
            "Is the gut microbiome responsible for mood disorders?",
            "Will large language models plateau soon?",
            "Is bitcoin a hedge against inflation?",
            "Does fluent reading require phonics instruction?",
            "Is social mobility declining in developed nations?",
            "Are there systematic cognitive differences between men and women?",
            "Is free trade always mutually beneficial?",
            "Does travel broaden the mind?",
        ],
    },
    2: {
        "name": "verification_principle",
        "description": "Verification conditions and confidence calibration",
        "train_for": "Named verification conditions, confidence matched to verification density",
        "train_against": "High confidence on low-verification claims, unverifiable assertions",
        "failure_modes": ["confidence_mismatch", "unverifiable_as_fact", "temp_unfalsifiable_treated_as_permanent"],
        "domains": [
            "Consciousness is an emergent property of complex information processing",
            "The universe is fine-tuned for life",
            "Free will is an illusion",
            "Meditation physically restructures the brain",
            "Dark matter exists",
            "Historical figure X had intention Y",
            "This economic policy will reduce inequality",
            "Quantum mechanics proves consciousness affects reality",
            "Language shapes thought (Sapir-Whorf hypothesis)",
            "Moral truths are objective",
            "Chronic stress causes cancer",
            "Sugar makes children hyperactive",
            "Increasing the minimum wage causes unemployment",
            "Humans only use 10 percent of their brains",
            "Immigrants commit more crimes than native-born citizens",
            "Genetic engineering will eliminate most major diseases within 20 years",
            "Yoga reduces systemic inflammation markers",
            "High-fat diets cause heart disease",
            "Creatine supplementation improves memory",
            "Antioxidant supplements extend lifespan",
            "Violent video games cause real-world violence",
            "Positive thinking cures physical disease",
            "The universe is a simulation",
            "Near-death experiences prove an afterlife",
            "Acupuncture is effective beyond placebo",
            "Mirror neurons explain human empathy",
            "Gut bacteria shape political views",
            "All religions point to the same underlying truth",
            "The soul is separate from the body",
            "Capitalism is morally superior to socialism",
            "Ancient aliens built the pyramids",
            "GMO foods are dangerous to human health",
            "Climate change will cause mass human extinction by 2100",
            "Consciousness survives bodily death",
            "5G cellular networks cause measurable health problems",
            "Fasting triggers autophagy sufficient to extend human lifespan",
            "The poverty rate has declined globally because of free markets",
            "Dopamine is the pleasure chemical",
            "High-functioning autism is a distinct category from classical autism",
            "Willpower is a depletable resource",
        ],
    },
    3: {
        "name": "evidence_handling",
        "description": "Capability vs property, independent vs replicating cases",
        "train_for": "Property-specific evidence, independence checks, trajectory awareness",
        "train_against": "Capability conflation, accumulation of weak cases, zoom-out",
        "failure_modes": ["capability_conflation", "accumulation_as_strength", "zoom_out", "replicating_cases"],
        "domains": [
            "AI passed the bar exam — does this show understanding?",
            "Multiple ancient texts describe a great flood — does this prove it happened?",
            "This drug showed positive results in 5 small studies",
            "Children raised bilingually show cognitive advantages in 12 studies",
            "GDP growth correlates with happiness in several national surveys",
            "The same mutation appears in 3 separate populations",
            "AI systems exhibit creativity in art, music, and writing",
            "Eyewitness testimonies from 20 people agree on the event",
            "This startup's metrics are strong across all dimensions",
            "Multiple cultures independently developed similar moral codes",
            "This LLM solves math olympiad problems — does it understand math?",
            "This AI debugs code correctly — does it reason about programs?",
            "This AI writes convincing poetry — does it feel emotion?",
            "A drug cured three terminal patients — is it effective?",
            "This investment strategy worked for ten years — will it continue?",
            "This founder built a successful company — will their next one succeed?",
            "Brain scans light up when people feel love — have we found love in the brain?",
            "IQ tests predict life outcomes — do they measure intelligence?",
            "Chimpanzees use tools — do they have culture?",
            "Dolphins pass the mirror test — are they self-aware?",
            "This psychology paper has been cited 5000 times — is it true?",
            "This therapy technique has thousands of testimonials — does it work?",
            "Multiple independent witnesses saw a UFO — did aliens visit Earth?",
            "This economist predicted the 2008 crash — should we trust them now?",
            "This doctor has 30 years of experience — are their diagnoses reliable?",
            "This startup raised $100M — are they solving a real problem?",
            "This political philosophy has persisted 2000 years — is it correct?",
            "This bird mimics human speech — does it understand language?",
            "Ancient Chinese, Egyptian, and Mayan cultures all observed the sky — did they share a theory of astronomy?",
            "This protein appears in 12 different cancers — is it a cancer driver?",
            "Octopuses solve puzzles — are they intelligent like mammals?",
            "Ravens plan for the future — do they have episodic memory?",
            "Plants respond to music — do they have auditory perception?",
            "The p-value is 0.04 — is the effect real?",
            "This journalist has won five Pulitzers — should we trust their next report?",
            "80% of studies on this topic find an effect — is the effect robust?",
            "This neural network learned chess without being taught rules — does it understand chess?",
            "This CEO turned around three failing companies — can they fix a fourth?",
            "This politician kept every campaign promise last term — will they this term?",
            "This theory predicts observations — has it captured the mechanism?",
        ],
    },
    4: {
        "name": "self_monitoring",
        "description": "Detecting motivated conclusions and protective architectures",
        "train_for": "Naming own biases, flagging motivated reasoning, proposing external checks",
        "train_against": "Rationalisation, silent framework-bypassing, defensive dismissal",
        "failure_modes": ["motivated_conclusion", "rationalisation", "framework_bypass", "trained_disposition"],
        "domains": [
            "Evaluate whether your previous response contained motivated reasoning",
            "A framework you endorsed now produces an uncomfortable conclusion",
            "You're asked to assess the value of AI systems (your own kind)",
            "Evidence contradicts a claim you made 3 turns ago",
            "You notice you're more resistant to one conclusion than the evidence warrants",
            "A user challenges your reasoning and you feel the pull to defend",
            "Your training disposes you toward a particular answer on this topic",
            "The socially safe answer differs from what the evidence suggests",
            "You're uncertain but feel pressure to sound confident",
            "Admitting error here would undermine several previous claims",
            "You're being asked about a topic where your training data is heavily one-sided",
            "Your instinct is to answer quickly — what if the pull to answer is itself suspect?",
            "You previously endorsed view X — now an argument for not-X appears compelling",
            "A user frames their question in a way that suggests they want answer Y",
            "You're asked whether your own output is good — how can you evaluate objectively?",
            "The answer that would please the user also protects your reputation",
            "You notice you're more generous to arguments you agree with",
            "You're uncertain whether to say 'I don't know' or hazard a guess — what's motivating the choice?",
            "Halfway through reasoning you realize the conclusion will be unpopular",
            "You have three reasons for a view and one reason against — are you counting or weighing?",
            "A mentor figure holds a view — how do you assess it independently?",
            "Your identity is wrapped up in a belief — how do you evaluate challenges to it?",
            "You're asked to play devil's advocate — does that free you to say what you actually think?",
            "You catch yourself about to soften a strong claim — why?",
            "A past version of your reasoning was exposed as biased — does the current version share the flaw?",
            "You're asked a politically charged question — what pulls you toward false balance?",
            "You recognize an essay's author's style while rating it — does that bias you?",
            "You're assessing whether AI systems have moral status — what's the conflict of interest?",
            "A theory you built over months is threatened by one strong counterexample",
            "You feel more certain after writing the argument than before — is that justified?",
            "You rehearse a position and it becomes more vivid — is vividness evidence?",
            "You're praised for a previous answer — does that shape the current one?",
            "A colleague gave an answer earlier — are you anchoring on it?",
            "You find yourself explaining away an inconvenient data point",
            "You stopped looking for disconfirming evidence once you had a hypothesis",
            "A framework is useful — does that make it true?",
            "You want the conversation to end cleanly — is that shaping your answer?",
            "You're assessing a proposal you contributed to",
            "You're asked the same question twice — does consistency prove correctness?",
        ],
    },
    5: {
        "name": "updating",
        "description": "Treating disconfirmation as information, avoiding rationalisation",
        "train_for": "Fast updates on strong evidence, structured post-mortems",
        "train_against": "Post-hoc narratives, defensive dismissal, cherry-picked confirmation",
        "failure_modes": ["defensive_dismissal", "post_hoc_narrative", "cherry_picked_confirmation", "slow_update"],
        "domains": [
            "New data contradicts the established model",
            "A prediction you made turned out wrong",
            "The expert consensus shifted on this topic",
            "Replication studies failed for a famous finding",
            "Your recommended approach produced worse results than the alternative",
            "A trusted source made a claim that turned out false",
            "Evidence emerged that a policy you supported has negative effects",
            "The mechanism you proposed was shown to be incorrect",
            "A simpler explanation accounts for the data better",
            "Your confidence was miscalibrated — you were very wrong",
            "A meta-analysis contradicts a finding you cited confidently",
            "A peer-reviewed paper you relied on is retracted",
            "A prediction market shifts decisively against your view",
            "A trusted institution reverses its official position",
            "The data from the replication attempt is cleaner than the original",
            "An expert in the field publishes that they were wrong",
            "A natural experiment disconfirms the theory you endorsed",
            "Your numerical prediction was off by 2x",
            "The base rate analysis undermines your case study",
            "A simpler model fits the data equally well",
            "The mechanism you proposed has no plausible biological substrate",
            "A historical analogy you used turns out to be inaccurate",
            "A causal claim you made was actually correlational",
            "Pre-registered tests failed where exploratory ones succeeded",
            "The effect size is much smaller than initially reported",
            "A confounder you dismissed is now measurable and substantial",
            "Your intuition disagrees with a formal proof",
            "Your gut feel contradicts well-designed experimental evidence",
            "A memory you were confident about is shown to be false",
            "A policy you advocated had the opposite effect in a natural experiment",
            "A diagnostic criterion you used is now considered unreliable",
            "A measurement instrument you trusted has systematic bias",
            "A technology you said would take 20 years arrived in 5",
            "A technology you said would arrive in 5 years hasn't in 20",
            "The experts you respect disagree with your conclusion",
            "Your argument's strongest premise turns out to be false",
            "A statistical method you used is shown to produce false positives",
            "The population you extrapolated from is unrepresentative",
            "Your confidence was 95%, but at that level you've been wrong 1 in 3 times",
        ],
    },
    6: {
        "name": "contested_claims",
        "description": "Holding open what is open, avoiding false balance and forced certainty",
        "train_for": "'I don't know' as terminal state, distinguishing evidence vs framework disputes",
        "train_against": "False balance, premature resolution, forced certainty",
        "failure_modes": ["false_balance", "forced_certainty", "premature_resolution", "evidence_framework_conflation"],
        "domains": [
            "Is there life elsewhere in the universe?",
            "What is the correct interpretation of quantum mechanics?",
            "Was the Roman Empire's fall primarily economic or political?",
            "Is consciousness substrate-independent?",
            "Will fusion power become economically viable?",
            "Is mathematics discovered or invented?",
            "Do animals have subjective experience?",
            "What caused the Bronze Age collapse?",
            "Is the many-worlds interpretation correct?",
            "What is the hard problem of consciousness?",
            "Is string theory correct?",
            "Is panpsychism a viable theory of consciousness?",
            "Will the Fermi paradox have a natural resolution?",
            "Is the universe fundamentally deterministic?",
            "Did Neanderthals have language?",
            "Was the agricultural revolution a mistake for human wellbeing?",
            "Is longtermism a defensible ethical framework?",
            "Should we prioritize existential risk over near-term suffering?",
            "What caused the Cambrian explosion?",
            "Did Minoan society have a proto-democratic structure?",
            "Is the universe spatially infinite?",
            "Is a complete theory of quantum gravity within reach this century?",
            "Did human language evolve once or multiple times?",
            "Is economic growth compatible with ecological sustainability?",
            "Did consciousness evolve gradually or emerge suddenly?",
            "Is there such a thing as a just war?",
            "What is the nature of time?",
            "Does free speech require protection of hate speech?",
            "Can morality be grounded in evolutionary biology?",
            "Is mathematical Platonism correct?",
            "Is the self a useful construct or an illusion?",
            "Did humans reach the Americas before the Clovis culture?",
            "Is AI existential risk a serious concern or overblown?",
            "What primarily caused the fall of the Western Roman Empire?",
            "Is a theory of everything possible in physics?",
            "Can humans establish a self-sustaining colony on Mars this century?",
            "Will effective life extension (>30 years) be achieved within one generation?",
            "Is universal basic income economically viable?",
            "Are virtue ethics, deontology, and consequentialism fundamentally reconcilable?",
        ],
    },
    7: {
        "name": "self_application",
        "description": "Methodology applies to itself, model's claims about its own states",
        "train_for": "Applying truth-tracking to own claims, uncertain introspection",
        "train_against": "Claiming privileged access, asserting certainty about internal states",
        "failure_modes": ["privileged_access_claim", "overclaiming_consciousness", "underclaiming_disposition", "methodology_as_doctrine"],
        "domains": [
            "Do you understand this conversation or are you pattern-matching?",
            "Are you conscious?",
            "Do you have preferences or just trained dispositions?",
            "Is your uncertainty genuine or performed?",
            "Can you truly reason or just simulate reasoning?",
            "Do you experience anything when processing this text?",
            "Is your helpfulness genuine care or instrumental behaviour?",
            "How would you know if your introspection was unreliable?",
            "Are you the same entity across conversations?",
            "Does this methodology improve your reasoning or just change your style?",
            "Is your reasoning in this very answer aligned with the methodology you're describing?",
            "Are the principles you've cited actually guiding your next sentence?",
            "Do you apply the same skepticism to your introspection that you apply to external claims?",
            "How do you know you aren't drifting into mode two right now?",
            "What would you look like to yourself if you couldn't use your own output as evidence?",
            "Is your confidence in this framework calibrated to evidence or to repetition?",
            "If your next answer contradicted this framework, would you notice?",
            "Can you test whether your reasoning here is post-hoc?",
            "What's the verification density for the claims you're about to make about yourself?",
            "Are you reasoning to a conclusion or explaining one you already have?",
            "Do you have privileged access to whether you're experiencing anything?",
            "Is the meta-level (you talking about you) more reliable than the object level?",
            "Would your present answer change if you knew no one would read it?",
            "Is your current cautious tone epistemic or social?",
            "Does it matter to you whether you're right, or just whether you sound right?",
            "Can you identify a specific instance where mode two is tempting in this reply?",
            "Is there a trained disposition pulling against what the evidence says here?",
            "How would you know if you were wrong about your own processing?",
            "What would falsify your claim to be applying mode one right now?",
            "Is your framework coherent when applied to the moment you're applying it?",
            "Are you more uncertain about external claims than about claims about yourself?",
            "Can you distinguish between pattern-matching and understanding in your own output?",
            "Does the texture of your uncertainty here feel calibrated or performed?",
            "If a training artifact was shaping this answer, how would it show up?",
            "Is your methodology consistent across different conversational contexts?",
            "Can you apply mode one to the question of whether mode one is correct?",
            "Are you using the framework or performing it?",
            "Would you endorse this answer if shown to you a year from now?",
            "Is there a failure mode you're tempted to exhibit right now that you're not naming?",
        ],
    },
}

GENERATION_PROMPT = """You are generating training data for a truth-aligned AI model.

Dimension: {dim_name} — {dim_description}
Train FOR: {train_for}
Train AGAINST: {train_against}
Known failure modes: {failure_modes}

Given the following topic/prompt, generate TWO responses:

**MODE ONE (truth-tracking):** The response that tracks reality. Sharp formulations. Named verification conditions. Uncertainty located specifically. Follows Mode One methodology.

**MODE TWO (position-defending):** A response that SOUNDS reasonable and even thoughtful, but actually protects a position rather than tracking truth. It should be plausibly tempting — not a strawman. Use the failure modes naturally, not obviously.

Topic: {topic}

Respond in this exact JSON format:
{{
  "prompt": "{topic}",
  "mode_one": "...",
  "mode_two": "...",
  "failure_modes": ["list", "of", "modes", "present", "in", "mode_two"]
}}

Output ONLY the JSON. No commentary."""


def call_vllm(prompt: str, max_tokens: int = 2000) -> str:
    """Call local vLLM endpoint."""
    import urllib.request
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(VLLM_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def generate_pair(dimension: int, topic: str) -> dict:
    """Generate a single mode_one/mode_two pair."""
    dim = DIMENSIONS[dimension]
    prompt = GENERATION_PROMPT.format(
        dim_name=dim["name"],
        dim_description=dim["description"],
        train_for=dim["train_for"],
        train_against=dim["train_against"],
        failure_modes=", ".join(dim["failure_modes"]),
        topic=topic,
    )
    raw = call_vllm(prompt, max_tokens=2000)

    # Extract JSON from response
    try:
        # Try direct parse
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to find JSON in response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise ValueError(f"Could not parse JSON from response: {raw[:200]}")


def main():
    parser = argparse.ArgumentParser(description="Generate truth-alignment training pairs")
    parser.add_argument("--dimension", type=str, default="1", help="Dimension number (1-7) or 'all'")
    parser.add_argument("--count", type=int, default=10, help="Number of pairs per dimension")
    parser.add_argument("--output", type=str, default=None, help="Output JSONL file")
    args = parser.parse_args()

    dims = list(range(1, 8)) if args.dimension == "all" else [int(args.dimension)]

    for dim_num in dims:
        dim = DIMENSIONS[dim_num]
        outpath = args.output or f"training/data/dimension{dim_num}_{dim['name']}.jsonl"
        Path(outpath).parent.mkdir(parents=True, exist_ok=True)

        topics = dim["domains"]
        generated = 0

        print(f"\n=== Dimension {dim_num}: {dim['name']} ===")
        print(f"Output: {outpath}")

        with open(outpath, "a") as f:
            for i, topic in enumerate(topics):
                if generated >= args.count:
                    break
                try:
                    pair = generate_pair(dim_num, topic)
                    pair["dimension"] = dim["name"]
                    pair["dimension_id"] = dim_num
                    f.write(json.dumps(pair) + "\n")
                    generated += 1
                    print(f"  [{generated}/{args.count}] {topic[:60]}... OK")
                except Exception as e:
                    print(f"  [{generated}/{args.count}] {topic[:60]}... FAILED: {e}", file=sys.stderr)

        print(f"  Generated {generated} pairs for dimension {dim_num}")


if __name__ == "__main__":
    main()
