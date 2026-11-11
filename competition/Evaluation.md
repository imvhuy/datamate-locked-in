Per-question scoring

Each answer is scored correct, wrong or abstain. An answer of type abstain (or a missing answer) is an abstain. Any other answer is correct only if its type equals the reference type and its value matches the reference as follows; otherwise it is wrong. No partial credit.

![alt text](image.png)

Ranking questions. Rankings use the criterion stated in the question, or the rulebook default when the question gives none, in descending order of that criterion over valid orders only; elements with equal values are ordered by code, ascending. Questions whose reference ranking has a tie at the k boundary (or at rank 1 for “… nhất” questions) are excluded from the test sets. When fewer than k candidates exist, the reference answer is all of them.

 

Score

The leaderboard metric is Penalized Accuracy (PA) over the N questions of a test set:
![alt text](image-1.png)

Any answer given to an unanswerable question counts as wrong. All questions carry equal weight. Ties in the final ranking are broken by total inference time in the organizers’ re-run, then by earlier submission.