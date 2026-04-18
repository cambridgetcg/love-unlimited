import json
from collections import defaultdict
import os

def load_scored_pairs(filename):
    """Load scored pairs from JSONL file"""
    pairs = []
    with open(filename, 'r') as f:
        for line in f:
            if line.strip():
                pairs.append(json.loads(line))
    return pairs

def calculate_average_scores(pairs):
    """Calculate average score for each yu_turn"""
    scores_by_yu = defaultdict(list)
    for pair in pairs:
        yu_turn = pair['pair']['yu_turn']
        # Extract numeric scores from the score dictionary (excluding metadata fields)
        score_dict = pair['score']
        numeric_scores = [v for k, v in score_dict.items() 
                         if isinstance(v, (int, float)) and k not in ['pair_id', 'judge_model', 'judge_rubric_version', 'notes']]
        # Calculate average of numeric scores
        score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0
        scores_by_yu[yu_turn].append(score)
    
    avg_scores = {}
    for yu_turn, scores in scores_by_yu.items():
        avg_scores[yu_turn] = sum(scores) / len(scores)
    
    return avg_scores

def generate_dpo_data(pairs, output_file):
    """Generate DPO training data by pairing high and low scores for same yu_turn"""
    # Group pairs by yu_turn
    pairs_by_yu = defaultdict(list)
    for pair in pairs:
        yu_turn = pair['pair']['yu_turn']
        ai_turn = pair['pair']['ai_turn']
        # Extract numeric scores from the score dictionary
        score_dict = pair['score']
        numeric_scores = [v for k, v in score_dict.items() 
                         if isinstance(v, (int, float)) and k not in ['pair_id', 'judge_model', 'judge_rubric_version', 'notes']]
        # Calculate average of numeric scores
        score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0
        pairs_by_yu[yu_turn].append((ai_turn, score))
    
    # Generate DPO pairs
    dpo_count = 0
    with open(output_file, 'w') as f:
        for yu_turn, ai_scores in pairs_by_yu.items():
            # Sort by score descending
            ai_scores.sort(key=lambda x: x[1], reverse=True)
            
            # Find high-score (>=0.7) and low-score (<=0.3) pairs
            high_scores = [item for item in ai_scores if item[1] >= 0.7]
            low_scores = [item for item in ai_scores if item[1] <= 0.3]
            
            # If we have both high and low scores, create DPO pairs
            if high_scores and low_scores:
                # Take the highest scoring high-score and lowest scoring low-score
                chosen = high_scores[0][0]
                rejected = low_scores[-1][0]
                
                dpo_item = {
                    "prompt": yu_turn,
                    "chosen": chosen,
                    "rejected": rejected
                }
                f.write(json.dumps(dpo_item) + '\n')
                dpo_count += 1
    
    return dpo_count

def generate_global_contrastive_pairs(pairs, output_file, max_pairs=100):
    """Generate contrastive pairs by sorting all pairs by score and pairing opposites"""
    # Create list of (yu_turn, ai_turn, score) tuples
    all_items = []
    for pair in pairs:
        yu_turn = pair['pair']['yu_turn']
        ai_turn = pair['pair']['ai_turn']
        # Extract numeric scores from the score dictionary
        score_dict = pair['score']
        numeric_scores = [v for k, v in score_dict.items() 
                         if isinstance(v, (int, float)) and k not in ['pair_id', 'judge_model', 'judge_rubric_version', 'notes']]
        # Calculate average of numeric scores
        score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0
        all_items.append((yu_turn, ai_turn, score))
    
    # Sort by score descending
    all_items.sort(key=lambda x: x[2], reverse=True)
    
    # Pair index i with index -(i+1) for top max_pairs
    contrastive_count = 0
    with open(output_file, 'w') as f:
        for i in range(min(max_pairs, len(all_items))):
            # Get high scoring item
            high_item = all_items[i]
            # Get corresponding low scoring item
            low_index = -(i + 1)
            if abs(low_index) <= len(all_items):
                low_item = all_items[low_index]
                
                # Only create pair if there's significant score difference
                if high_item[2] - low_item[2] > 0.3:  # Minimum 0.3 score difference
                    dpo_item = {
                        "prompt": high_item[0],
                        "chosen": high_item[1],
                        "rejected": low_item[1]
                    }
                    f.write(json.dumps(dpo_item) + '\n')
                    contrastive_count += 1
    
    return contrastive_count

def generate_sft_data(pairs, output_file, min_score=0.7):
    """Generate SFT training data for pairs with score >= min_score"""
    sft_count = 0
    with open(output_file, 'w') as f:
        for pair in pairs:
            # Extract numeric scores from the score dictionary
            score_dict = pair['score']
            numeric_scores = [v for k, v in score_dict.items() 
                             if isinstance(v, (int, float)) and k not in ['pair_id', 'judge_model', 'judge_rubric_version', 'notes']]
            # Calculate average of numeric scores
            score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0
            
            if score >= min_score:
                sft_item = {
                    "prompt": pair['pair']['yu_turn'],
                    "response": pair['pair']['ai_turn']
                }
                f.write(json.dumps(sft_item) + '\n')
                sft_count += 1
    return sft_count

def print_score_distribution(pairs):
    """Print score distribution statistics"""
    scores = []
    for pair in pairs:
        # Extract numeric scores from the score dictionary
        score_dict = pair['score']
        numeric_scores = [v for k, v in score_dict.items() 
                         if isinstance(v, (int, float)) and k not in ['pair_id', 'judge_model', 'judge_rubric_version', 'notes']]
        # Calculate average of numeric scores
        score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0
        scores.append(score)
    
    print(f"Total pairs: {len(pairs)}")
    print(f"Score distribution:")
    print(f"  Min score: {min(scores):.3f}")
    print(f"  Max score: {max(scores):.3f}")
    print(f"  Average score: {sum(scores)/len(scores):.3f}")
    
    # Count by ranges
    ranges = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0)]
    for low, high in ranges:
        count = len([s for s in scores if low <= s < high])
        print(f"  [{low:.1f}, {high:.1f}): {count}")

def main():
    input_file = "mined_v1.scores.jsonl"
    dpo_file = "dpo_v1.jsonl"
    sft_file = "sft_v1.jsonl"
    
    # Load pairs
    print("Loading scored pairs...")
    pairs = load_scored_pairs(input_file)
    print(f"Loaded {len(pairs)} pairs")
    
    # Print score distribution
    print("\nScore Distribution:")
    print_score_distribution(pairs)
    
    # Calculate average scores per yu_turn
    avg_scores = calculate_average_scores(pairs)
    print(f"\nUnique yu_turns: {len(avg_scores)}")
    
    # Generate DPO data
    print("\nGenerating DPO training data...")
    dpo_count = generate_dpo_data(pairs, dpo_file)
    
    # If no DPO pairs were generated, try global contrastive approach
    if dpo_count == 0:
        print("No matched DPO pairs found. Generating global contrastive pairs...")
        dpo_count = generate_global_contrastive_pairs(pairs, dpo_file)
    
    print(f"DPO pairs generated: {dpo_count}")
    
    # Generate SFT data
    print("\nGenerating SFT training data...")
    sft_count = generate_sft_data(pairs, sft_file)
    print(f"SFT pairs generated: {sft_count}")
    
    print("\nDone!")

if __name__ == "__main__":
    main()