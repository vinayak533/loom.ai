"""Machine Learning Basics."""

from __future__ import annotations

COURSE = {
    "id": "ml-basics",
    "title": "Machine Learning Basics",
    "short_title": "ML Basics",
    "subtitle": "From training data to a model you can trust",
    "difficulty": "beginner",
    "tags": ["AI", "Data"],
    "description": (
        "Machine learning is fitting a function to data and then finding out "
        "whether it generalises. This course covers the shape of the field, "
        "supervised learning, the bias–variance trade-off, how models are "
        "actually trained, honest evaluation, feature engineering, and the "
        "step from a notebook to something in production."
    ),
    "objectives": [
        "Distinguish supervised, unsupervised and reinforcement learning",
        "Split data correctly and diagnose overfitting and underfitting",
        "Explain gradient descent and the role of the loss function",
        "Choose evaluation metrics that match the problem, not the default",
        "Prepare features without leaking information from the future",
        "Understand what changes when a model is deployed",
    ],
    "resources": [
        {"kind": "video", "title": "3Blue1Brown — Neural networks series", "url": "https://www.youtube.com/@3blue1brown"},
        {"kind": "video", "title": "StatQuest with Josh Starmer", "url": "https://www.youtube.com/@statquest"},
        {"kind": "doc", "title": "scikit-learn — User guide", "url": "https://scikit-learn.org/stable/user_guide.html"},
    ],
    "chapters": [
        {
            "id": "landscape",
            "title": "What machine learning is",
            "topic": "Foundations",
            "summary": "Learning from data instead of writing the rules — and the three main settings.",
            "minutes": 14,
            "body": """
## Programs that are fitted, not written

Traditional software: you write rules; data goes in, answers come out. Machine
learning: you supply data *and* answers; the process produces the rules.

That inversion is the whole idea, and it explains everything downstream — why
data quality dominates, why models fail on inputs unlike their training data,
and why "the model is wrong" is usually "the data was".

## The three settings

**Supervised** — labelled examples, learn input → output.
- *Classification*: discrete labels (spam / not spam, which digit).
- *Regression*: continuous values (price, temperature).

**Unsupervised** — no labels, find structure.
- Clustering (k-means), dimensionality reduction (PCA), anomaly detection.

**Reinforcement** — an agent takes actions in an environment and learns from
rewards. Games, robotics, control.

Most production ML is supervised, because most business problems come with
historical outcomes attached.

## Vocabulary

- **Feature** — an input variable. **Label** / target — what you predict.
- **Sample** — one row. **Model** — the fitted function.
- **Parameters** — learned from data (weights). **Hyperparameters** — set by
  you (learning rate, depth, k).
- **Training** — fitting parameters. **Inference** — using the model.

## When not to use ML

- The rule is known and stable → write the rule. A regex beats a classifier for
  a fixed format.
- You have very little data → start with a heuristic and collect data.
- Errors are unacceptable and unexplainable → a probabilistic system may be the
  wrong tool.
- No feedback loop exists → you will never know whether it works.

A simple baseline is not a formality. "Predict the most common class" or "assume
tomorrow equals today" is often surprisingly hard to beat, and if you cannot beat
it, you have learned something important early.
""",
            "concepts": [
                ("Supervised learning", "Learning a mapping from inputs to labelled outputs."),
                ("Unsupervised learning", "Finding structure in unlabelled data."),
                ("Feature", "An input variable used by the model."),
                ("Hyperparameter", "A setting chosen before training rather than learned from data."),
            ],
            "takeaways": [
                "ML infers rules from data instead of you writing them",
                "Classification predicts categories; regression predicts quantities",
                "Parameters are learned; hyperparameters are chosen",
                "Always establish a trivial baseline before modelling",
            ],
            "resources": [
                {"kind": "doc", "title": "scikit-learn — An introduction to machine learning", "url": "https://scikit-learn.org/stable/tutorial/basic/tutorial.html"},
                {"kind": "video", "title": "StatQuest — Machine learning playlist", "url": "https://www.youtube.com/@statquest"},
            ],
            "video": {"id": "aircAruvnKk", "title": "But what is a neural network?", "channel": "3Blue1Brown"},
        },
        {
            "id": "supervised",
            "title": "Supervised learning & core algorithms",
            "topic": "Algorithms",
            "summary": "Linear models, trees and ensembles — and when each is the right first choice.",
            "minutes": 17,
            "body": """
## Linear and logistic regression

**Linear regression** fits `y = w·x + b` by minimising squared error. Fast,
interpretable (each weight is that feature's effect), and a strong baseline for
regression.

**Logistic regression** puts a sigmoid on that output to produce a probability:

```
p = 1 / (1 + e^-(w·x + b))
```

Despite the name it is a classifier. It remains the default first model for
tabular binary classification, because it is fast, calibrated and explainable.

## Decision trees

A tree of if/else splits chosen to reduce impurity (Gini or entropy).

```
                  income > 50k?
                 /            \\
          age > 30?          reject
          /      \\
     approve    review
```

Readable and needs no feature scaling. Left unconstrained, a single tree
memorises the training data — depth and minimum-leaf-size limits are not
optional.

## Ensembles

- **Random forest** — many trees on bootstrapped samples with random feature
  subsets; average them. Reduces variance, very hard to break.
- **Gradient boosting** (XGBoost, LightGBM, CatBoost) — trees fitted
  sequentially, each correcting the previous one's residuals. Usually the best
  performer on tabular data, and it will overfit if you let it.

On structured/tabular problems, gradient-boosted trees beat neural networks more
often than not. Reach for deep learning for images, audio, text and other
high-dimensional unstructured data.

## Others worth knowing

- **k-NN** — predict from the k nearest training points. No training; slow
  inference; needs scaling.
- **SVM** — maximum-margin separator, effective in high dimensions.
- **Naive Bayes** — fast probabilistic baseline, still respectable for text.

## Choosing

```
Tabular data          → gradient boosting; logistic/linear regression first
Images, audio, text   → neural networks (usually a pretrained one, fine-tuned)
Need explainability   → linear model or a shallow tree
Tiny dataset          → simple model, heavy regularisation, cross-validation
```

Start with the simplest model that could work. It sets the bar and it tells you
whether the *data* carries signal at all — which is the question that actually
matters.
""",
            "concepts": [
                ("Logistic regression", "A linear model producing calibrated class probabilities via a sigmoid."),
                ("Decision tree", "Recursive splits on features chosen to reduce impurity."),
                ("Random forest", "Averaging many decorrelated trees to reduce variance."),
                ("Gradient boosting", "Sequentially fitting trees to the previous ensemble's errors."),
            ],
            "takeaways": [
                "Logistic regression is a classifier and the right first tabular model",
                "An unconstrained decision tree memorises its training data",
                "Gradient-boosted trees usually win on tabular data; neural networks on unstructured data",
                "The simplest model tells you whether the data has signal at all",
            ],
            "resources": [
                {"kind": "doc", "title": "scikit-learn — Supervised learning", "url": "https://scikit-learn.org/stable/supervised_learning.html"},
                {"kind": "video", "title": "StatQuest — Decision trees and random forests", "url": "https://www.youtube.com/@statquest"},
            ],
            "video": {"query": "statquest logistic regression decision trees explained", "title": "Core algorithms"},
        },
        {
            "id": "overfitting",
            "title": "Overfitting, splits and the bias–variance trade-off",
            "topic": "Generalisation",
            "summary": "The central problem: performing well on data you have never seen.",
            "minutes": 17,
            "body": """
## The only thing that matters

A model that memorises its training data is useless. The goal is
**generalisation** — performance on data the model has never seen.

## Splitting

```
train (60–80%)   fit parameters
validation (10–20%)  tune hyperparameters, choose the model
test (10–20%)    touched once, at the very end
```

The test set is not a development tool. Every time you look at it and change
something, you leak a little of it into your decisions and your final number
becomes optimistic.

**Time series must be split chronologically.** A random split lets the model
train on the future and predict the past — the score will be excellent and the
model worthless.

## Bias and variance

- **High bias (underfitting)** — the model is too simple. Poor on training
  *and* validation data.
- **High variance (overfitting)** — the model is too flexible. Excellent on
  training data, poor on validation.

| Symptom | Diagnosis | Fix |
|---|---|---|
| train 60%, val 58% | underfitting | more capacity, better features, train longer |
| train 99%, val 71% | overfitting | more data, regularisation, simpler model, early stopping |
| train 95%, val 94% | fine | ship it and monitor |

The gap between training and validation performance is the single most
informative number in applied ML.

## Regularisation

- **L2 (ridge)** — penalises large weights; shrinks them smoothly.
- **L1 (lasso)** — drives weights to exactly zero; performs feature selection.
- **Dropout** — randomly disables units during training (neural networks).
- **Early stopping** — stop when validation loss starts rising.
- **More data** — the most effective regulariser there is.

## Cross-validation

```python
from sklearn.model_selection import cross_val_score, StratifiedKFold

scores = cross_val_score(model, X, y, cv=StratifiedKFold(5), scoring="f1")
print(scores.mean(), scores.std())
```

k-fold uses every sample for both training and validation across folds. Essential
on small datasets, where a single split's score is mostly noise. Report the
standard deviation too — a 0.82 ± 0.11 is not the same result as 0.82 ± 0.01.

## Leakage

Any information in the training data that will not exist at prediction time.
Classic sources: fitting a scaler on the full dataset before splitting,
including a column derived from the target, or joining a table that was updated
after the outcome. Symptom: implausibly good validation scores. Suspect leakage
before celebrating.
""",
            "concepts": [
                ("Generalisation", "Performance on unseen data — the actual objective."),
                ("Bias–variance trade-off", "Too-simple models underfit; too-flexible models overfit."),
                ("Regularisation", "Constraining a model to reduce variance."),
                ("Data leakage", "Training-time access to information unavailable at prediction time."),
            ],
            "takeaways": [
                "Touch the test set once, at the end",
                "Split time series chronologically — never randomly",
                "The train/validation gap diagnoses bias versus variance",
                "Suspiciously good scores usually mean leakage, not success",
            ],
            "resources": [
                {"kind": "doc", "title": "scikit-learn — Cross-validation", "url": "https://scikit-learn.org/stable/modules/cross_validation.html"},
                {"kind": "video", "title": "StatQuest — Bias and variance", "url": "https://www.youtube.com/@statquest"},
            ],
            "video": {"query": "statquest bias variance tradeoff cross validation explained", "title": "Bias, variance and validation"},
        },
        {
            "id": "training",
            "title": "How models learn",
            "topic": "Training",
            "summary": "Loss functions, gradient descent and the hyperparameters that decide whether it works.",
            "minutes": 17,
            "body": """
## Loss

A **loss function** turns "how wrong is this prediction" into a number. Training
is minimising it.

- Regression: **MSE** (penalises large errors heavily; sensitive to outliers) or
  **MAE** (robust, less smooth).
- Classification: **cross-entropy**, which punishes confident wrong answers far
  more than uncertain ones.

Choosing the loss *is* stating the objective. If false negatives cost ten times
more than false positives, an unweighted loss encodes the opposite belief.

## Gradient descent

The gradient points uphill; step the opposite way.

```python
for epoch in range(epochs):
    for X_batch, y_batch in batches:
        preds = model(X_batch)
        loss = loss_fn(preds, y_batch)
        grads = backprop(loss)
        weights -= learning_rate * grads
```

- **Batch** — the whole dataset per step. Stable, slow, memory-hungry.
- **Stochastic** — one sample per step. Noisy, fast.
- **Mini-batch** (32–512) — the practical default.

## Learning rate

The most important hyperparameter, by a distance.

- Too high → loss oscillates or becomes NaN.
- Too low → training crawls and stalls in a poor region.

Use a schedule: warm up, then decay. **Adam** adapts per-parameter rates and is
a sound default; SGD with momentum still wins on some vision tasks.

## Reading the training curve

```
loss
 │ ╲___                  training loss
 │     ╲________
 │      ╲___
 │          ╲____/‾‾‾‾   validation loss  ← rising: overfitting starts here
 └────────────────────── epochs
```

Training loss falling while validation loss rises is textbook overfitting — stop
at the minimum (early stopping). Training loss not falling at all means the
learning rate, the features or the label mapping is wrong, and no amount of
extra epochs will fix it.

## Neural networks, briefly

Layers of weighted sums with a non-linearity (ReLU) between them. Depth lets the
network compose features; the non-linearity is what stops the whole stack from
collapsing into a single linear layer. Backpropagation is the chain rule applied
to compute every gradient in one backward pass.

## Practical hygiene

- Set a random seed and record it.
- Normalise inputs — gradient descent converges far better on comparable scales.
- Shuffle training data; never shuffle a time series split.
- Log every hyperparameter with every result, or you will not be able to explain
  your best run.
""",
            "concepts": [
                ("Loss function", "The scalar measure of error being minimised."),
                ("Gradient descent", "Iteratively stepping parameters opposite the gradient of the loss."),
                ("Learning rate", "The step size — the most consequential hyperparameter."),
                ("Early stopping", "Halting training when validation loss begins to rise."),
            ],
            "takeaways": [
                "The loss function encodes the objective, including error costs",
                "Mini-batch gradient descent is the practical default",
                "Rising validation loss with falling training loss means stop now",
                "Normalise inputs, seed the run, and log every hyperparameter",
            ],
            "resources": [
                {"kind": "video", "title": "3Blue1Brown — Gradient descent", "url": "https://www.youtube.com/@3blue1brown"},
                {"kind": "doc", "title": "scikit-learn — SGD", "url": "https://scikit-learn.org/stable/modules/sgd.html"},
            ],
            "video": {"query": "gradient descent backpropagation explained 3blue1brown", "title": "How models learn"},
        },
        {
            "id": "evaluation",
            "title": "Evaluation & metrics",
            "topic": "Metrics",
            "summary": "Accuracy is usually the wrong metric — choose one that matches the cost of being wrong.",
            "minutes": 17,
            "body": """
## The confusion matrix

```
                predicted
                pos     neg
actual  pos     TP      FN      ← missed
        neg     FP      TN
                ↑ false alarm
```

Everything else is derived from these four numbers.

## The metrics

```
accuracy  = (TP + TN) / total
precision = TP / (TP + FP)     of those flagged, how many were right
recall    = TP / (TP + FN)     of the real positives, how many did we find
F1        = harmonic mean of precision and recall
```

**Accuracy is misleading on imbalanced data.** A fraud detector where 1% of
transactions are fraudulent scores 99% by predicting "never fraud" — and is
worthless.

Choose by the cost of each error:

- **Recall matters** when a miss is expensive: disease screening, fraud, safety.
- **Precision matters** when a false alarm is expensive: spam filtering,
  automated account suspension.
- They trade off. Moving the decision threshold moves you along that curve.

## Threshold and curves

Most classifiers output a probability. The threshold is a *product* decision,
not a modelling one.

- **ROC-AUC** — ranking quality across all thresholds; optimistic on heavily
  imbalanced data.
- **PR-AUC** — precision–recall area; the right curve when positives are rare.

## Regression metrics

- **MAE** — mean absolute error, in the target's units, robust.
- **RMSE** — penalises large errors more; same units.
- **R²** — proportion of variance explained; can be negative for a bad model.

## Beyond a single number

- **Slice the metrics.** Overall 92% can hide 61% for one segment. Evaluate per
  cohort — this is where fairness problems become visible.
- **Look at the errors.** Read fifty misclassified examples. It is the fastest
  route to a real insight, and it usually reveals a labelling problem.
- **Compare against the baseline.** A model that beats "predict the majority
  class" by one point is not a model.

## Calibration

A model is calibrated when among predictions of 0.8, roughly 80% are positive.
Tree ensembles are often poorly calibrated. If a downstream decision uses the
probability as a probability — expected value, thresholds, pricing —
calibration matters as much as accuracy.
""",
            "concepts": [
                ("Precision vs recall", "Correctness of positive predictions versus coverage of actual positives."),
                ("Class imbalance", "Skewed label distributions that make accuracy uninformative."),
                ("PR-AUC", "Precision–recall curve area, preferred when positives are rare."),
                ("Calibration", "Whether predicted probabilities match observed frequencies."),
            ],
            "takeaways": [
                "Accuracy on imbalanced data is a trap",
                "Pick precision or recall according to which error costs more",
                "The decision threshold is a product choice, tuned after training",
                "Slice metrics by cohort and read the actual errors",
            ],
            "resources": [
                {"kind": "doc", "title": "scikit-learn — Model evaluation", "url": "https://scikit-learn.org/stable/modules/model_evaluation.html"},
                {"kind": "video", "title": "StatQuest — ROC and AUC", "url": "https://www.youtube.com/@statquest"},
            ],
            "video": {"query": "statquest precision recall roc auc confusion matrix explained", "title": "Evaluation metrics"},
        },
        {
            "id": "features",
            "title": "Data & feature engineering",
            "topic": "Features",
            "summary": "Where most of the achievable improvement actually lives.",
            "minutes": 16,
            "body": """
## Data quality dominates

Better features beat a better algorithm almost every time. Time spent
understanding and cleaning the data returns more than time spent tuning
hyperparameters — reliably, and by a wide margin.

## Missing values

```python
df["age"].fillna(df["age"].median())        # numeric: median is robust
df["city"].fillna("unknown")                # categorical: an explicit category
df["income_missing"] = df["income"].isna()  # missingness is often a signal
```

Never drop rows reflexively — if data is missing systematically, dropping
introduces bias. Ask *why* it is missing first.

## Categorical encoding

- **One-hot** — a column per category. Safe; explodes on high cardinality.
- **Ordinal** — integers. Only when the order is real (small < medium < large).
- **Target encoding** — replace with the mean target for that category.
  Powerful and a classic leakage source; compute it inside cross-validation
  folds only.

## Scaling

```python
from sklearn.preprocessing import StandardScaler
scaler = StandardScaler().fit(X_train)      # fit on TRAIN ONLY
X_train_s, X_test_s = scaler.transform(X_train), scaler.transform(X_test)
```

Required for k-NN, SVM, neural networks and any distance- or gradient-based
method. Irrelevant for trees. Fitting the scaler on the full dataset is the most
common leak in beginner code.

## Creating features

- **Dates** → day of week, month, is-weekend, days-since-event. A raw timestamp
  is nearly useless to most models.
- **Ratios and differences** — `price / area` often beats both columns.
- **Aggregates** — a user's mean, count, recency. Compute them from data
  *strictly before* the prediction time.
- **Text** → TF-IDF, or embeddings for anything semantic.

## Pipelines

```python
from sklearn.pipeline import Pipeline

pipe = Pipeline([("scale", StandardScaler()), ("model", LogisticRegression())])
pipe.fit(X_train, y_train)         # every step fits on train only
```

A pipeline makes leakage structurally difficult and guarantees that training and
inference apply exactly the same transformations — which is also the most
common source of train/serve skew.

## Imbalance

Class weights first (`class_weight="balanced"`), resampling second, synthetic
methods like SMOTE last. And never resample the validation or test set — those
must reflect reality.
""",
            "concepts": [
                ("Target encoding", "Replacing a category with its mean target value — leak-prone if done outside folds."),
                ("Feature scaling", "Normalising ranges for distance- and gradient-based models."),
                ("Pipeline", "A composed sequence of transformations fitted only on training data."),
                ("Train/serve skew", "Differences between training-time and inference-time feature computation."),
            ],
            "takeaways": [
                "Better features beat better algorithms",
                "Missingness is often itself informative — encode it",
                "Fit scalers and encoders on training data only, inside a pipeline",
                "Never resample validation or test sets",
            ],
            "resources": [
                {"kind": "doc", "title": "scikit-learn — Preprocessing", "url": "https://scikit-learn.org/stable/modules/preprocessing.html"},
                {"kind": "doc", "title": "scikit-learn — Pipelines", "url": "https://scikit-learn.org/stable/modules/compose.html"},
            ],
            "video": {"query": "feature engineering machine learning tutorial encoding scaling", "title": "Feature engineering"},
        },
        {
            "id": "workflow",
            "title": "The end-to-end workflow",
            "topic": "Workflow",
            "summary": "From a question to a trained model, without fooling yourself.",
            "minutes": 15,
            "body": """
## The loop

```
1. Frame the problem       what decision does this change?
2. Get and inspect data    distributions, missingness, duplicates, labels
3. Baseline                majority class / last value / a simple rule
4. Split                   train / validation / test (chronological if temporal)
5. Preprocess in a pipeline
6. Train a simple model    logistic regression or a small tree
7. Evaluate on validation  the metric that matches the cost
8. Iterate                 features first, then model, then hyperparameters
9. Test once               report honestly
10. Ship, monitor, retrain
```

Steps 1 and 2 receive the least attention and cause the most failures.

## Framing

"Predict churn" is not a problem statement. "Identify accounts likely to cancel
within 30 days, so the retention team can contact the top 200 each week" is: it
implies the label, the horizon, the metric (precision@200) and the baseline.

If no decision changes based on the prediction, do not build the model.

## Inspect before modelling

Distributions, missingness, duplicates, obvious outliers, label balance — and
whether the labels are even correct. Mislabelled training data caps your
achievable performance, and no model fixes it.

## Tuning

```python
from sklearn.model_selection import RandomizedSearchCV

search = RandomizedSearchCV(pipe, param_distributions, n_iter=40,
                            cv=5, scoring="average_precision", random_state=0)
```

Random search beats grid search for the same budget, because most
hyperparameters do not matter and grid search spends equal effort on all of
them. Tune last: features and data first, hyperparameters at the end, for the
final few percent.

## Reproducibility

Seed everything. Version the data as well as the code — a result you cannot
reproduce is an anecdote. Log parameters, metrics and artefacts per run
(MLflow, Weights & Biases, or a disciplined CSV).

## Being honest with yourself

The failure mode is not a bad model. It is a good-looking number you believe.
Three habits guard against it:

1. Hold the test set out completely until the end.
2. When a result looks too good, hunt for leakage before celebrating.
3. Compare to the baseline every time, and report the gap, not the raw score.
""",
            "concepts": [
                ("Problem framing", "Defining the decision, label, horizon and metric before modelling."),
                ("Baseline", "The trivial predictor any model must beat to be worth deploying."),
                ("Random search", "Sampling hyperparameter combinations rather than exhausting a grid."),
                ("Reproducibility", "Seeds, data versions and run logs that let a result be re-obtained."),
            ],
            "takeaways": [
                "If no decision changes, do not build the model",
                "Inspect and clean data before touching an algorithm",
                "Iterate features first, hyperparameters last",
                "Report the gap over the baseline, not the raw score",
            ],
            "resources": [
                {"kind": "doc", "title": "scikit-learn — Model selection", "url": "https://scikit-learn.org/stable/model_selection.html"},
                {"kind": "doc", "title": "Google — Rules of Machine Learning", "url": "https://developers.google.com/machine-learning/guides/rules-of-ml"},
            ],
            "video": {"query": "end to end machine learning project workflow tutorial", "title": "The ML workflow"},
        },
        {
            "id": "production",
            "title": "Models in production",
            "topic": "Production",
            "summary": "Serving, drift, monitoring — and why accuracy is not the deployment criterion.",
            "minutes": 15,
            "body": """
## Serving

- **Batch** — score everything nightly, store the results, serve from a table.
  Simplest and correct for anything not needed in real time.
- **Online** — an endpoint that scores per request. Needs latency budgets,
  autoscaling and a fallback.
- **Streaming** — score events as they arrive.

Choose the simplest that meets the need. Batch scoring solves more production
problems than any serving framework.

## Train/serve skew

The most common production failure: features computed one way in training and
another way at inference. A pandas transformation in the notebook and a
hand-written SQL query in the service will diverge — and the model degrades
silently, because nothing errors.

Share the transformation code, or a feature store, between both paths.

## Drift

- **Data drift** — the input distribution moves (new user demographics, a
  changed upstream field).
- **Concept drift** — the relationship between inputs and target moves (fraud
  patterns adapt; a competitor changes prices).

Both degrade a model that has not changed at all. Monitor input distributions,
prediction distributions and — where labels eventually arrive — live accuracy.

## What to monitor

1. Prediction distribution vs training distribution.
2. Feature distributions and missingness rates.
3. Live metrics once labels arrive (often days later).
4. Latency, error rate and fallback rate.
5. Business outcome — the metric the model was built to move.

The last one is the only one that decides whether the model is worth keeping.

## Deployment practice

- **Shadow mode** — run the model on live traffic without acting on it, and
  compare.
- **A/B test** — the only way to establish a causal effect on the business
  metric. Offline accuracy predicts it poorly.
- **A fallback path** — a heuristic or the previous model, used when inference
  fails or a feature is unavailable.
- **Versioning** — the model, the data and the code together, so you can roll
  back to something that actually reproduces.

## Responsibility

- Check performance **per demographic slice**, not only overall.
- Know whether the training data encodes a historical bias you are about to
  automate.
- Keep a human in the loop where a decision materially affects someone.
- Be able to explain a decision when a person asks — for many domains that is a
  legal requirement, not a nicety.
""",
            "concepts": [
                ("Train/serve skew", "Divergence between training-time and serving-time feature computation."),
                ("Data drift", "A change in the input distribution over time."),
                ("Concept drift", "A change in the relationship between inputs and target."),
                ("Shadow deployment", "Running a model on live traffic without using its output."),
            ],
            "takeaways": [
                "Batch scoring solves most problems; reach for online serving when latency demands it",
                "Train/serve skew degrades models silently — share the transformation code",
                "Monitor input and prediction distributions, not just accuracy",
                "Offline accuracy predicts business impact poorly — A/B test",
            ],
            "resources": [
                {"kind": "doc", "title": "Google — MLOps: continuous delivery for ML", "url": "https://cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning"},
                {"kind": "doc", "title": "Google — Rules of Machine Learning", "url": "https://developers.google.com/machine-learning/guides/rules-of-ml"},
            ],
            "video": {"query": "machine learning production mlops model drift monitoring", "title": "ML in production"},
            "notes": """
Rule 1 from Google's Rules of ML is still the best advice in the field: don't be
afraid to launch a product without machine learning. A heuristic that ships this
week beats a model that ships next quarter, and it gives you the data and the
baseline the model will eventually need.
""",
        },
    ],
    "exams": [
        {
            "id": "ml-exam-1",
            "title": "ML — Foundations Assessment",
            "description": "Covers chapters 1–4: the landscape, algorithms, generalisation, training.",
            "chapter_ids": ["landscape", "supervised", "overfitting", "training"],
            "questions": [
                {
                    "type": "mcq", "topic": "Foundations", "chapter_id": "landscape",
                    "prompt": "What is the difference between a parameter and a hyperparameter?",
                    "options": [
                        "Parameters are integers, hyperparameters are floats",
                        "Parameters are learned from data; hyperparameters are set before training",
                        "Hyperparameters are learned; parameters are fixed",
                        "There is no difference",
                    ],
                    "answer": 1,
                    "explanation": "Weights are parameters. Learning rate, tree depth and k are hyperparameters you choose and tune.",
                },
                {
                    "type": "truefalse", "topic": "Foundations", "chapter_id": "landscape",
                    "prompt": "Predicting house prices from features is a classification problem.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. A continuous target is regression; classification predicts discrete categories.",
                },
                {
                    "type": "mcq", "topic": "Algorithms", "chapter_id": "supervised",
                    "prompt": "Despite its name, logistic regression is used for:",
                    "options": ["Regression on continuous targets", "Classification, via predicted probabilities", "Clustering", "Dimensionality reduction"],
                    "answer": 1,
                    "explanation": "A sigmoid maps the linear output to a probability, which is thresholded into a class.",
                },
                {
                    "type": "scenario", "topic": "Algorithms", "chapter_id": "supervised",
                    "prompt": "You have 50,000 rows of tabular customer data and need the strongest predictive accuracy. What is the sensible first serious model?",
                    "options": [
                        "A deep neural network",
                        "Gradient-boosted trees, after a logistic-regression baseline",
                        "k-nearest neighbours on raw features",
                        "A transformer",
                    ],
                    "answer": 1,
                    "explanation": "Boosted trees dominate tabular problems. A simple baseline first tells you whether the data has signal at all.",
                },
                {
                    "type": "mcq", "topic": "Generalisation", "chapter_id": "overfitting",
                    "prompt": "Training accuracy is 99%, validation accuracy is 71%. What is happening?",
                    "options": ["Underfitting", "Overfitting", "Data leakage", "The learning rate is too low"],
                    "answer": 1,
                    "explanation": "A large train/validation gap is high variance. Fix with more data, regularisation, a simpler model or early stopping.",
                },
                {
                    "type": "scenario", "topic": "Generalisation", "chapter_id": "overfitting",
                    "prompt": "A model predicting next month's sales scores 0.97 R² on a random split but fails in production. What is the likely cause?",
                    "options": [
                        "Too few trees",
                        "A random split on time-series data let the model train on the future",
                        "The learning rate was too high",
                        "Insufficient regularisation only",
                    ],
                    "answer": 1,
                    "explanation": "Temporal data must be split chronologically, or the evaluation measures interpolation rather than forecasting.",
                },
                {
                    "type": "truefalse", "topic": "Generalisation", "chapter_id": "overfitting",
                    "prompt": "It is fine to check test-set performance repeatedly while iterating, as long as you do not train on it.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Each look leaks information into your decisions; the final number becomes optimistic. That is what validation is for.",
                },
                {
                    "type": "mcq", "topic": "Training", "chapter_id": "training",
                    "prompt": "Which hyperparameter most often determines whether training works at all?",
                    "options": ["Batch size", "Learning rate", "Number of epochs", "Random seed"],
                    "answer": 1,
                    "explanation": "Too high and the loss diverges; too low and training stalls. Everything else is secondary.",
                },
                {
                    "type": "code", "topic": "Training", "chapter_id": "training", "language": "text",
                    "prompt": "Training loss keeps falling while validation loss has been rising for 10 epochs. What should happen?",
                    "code": "epoch 20: train 0.31  val 0.44\nepoch 25: train 0.22  val 0.49\nepoch 30: train 0.15  val 0.56",
                    "options": [
                        "Train longer — the validation loss will come back down",
                        "Stop at the validation minimum (early stopping) and regularise",
                        "Increase the learning rate",
                        "Remove the validation set",
                    ],
                    "answer": 1,
                    "explanation": "This is textbook overfitting. The best model was at the validation minimum, not at the last epoch.",
                },
                {
                    "type": "mcq", "topic": "Training", "chapter_id": "training",
                    "prompt": "Why does a neural network need a non-linear activation between layers?",
                    "options": [
                        "To speed up training",
                        "Without it, stacked linear layers collapse into a single linear transformation",
                        "To normalise the inputs",
                        "To prevent gradient computation",
                    ],
                    "answer": 1,
                    "explanation": "Non-linearity is what gives depth any expressive power at all.",
                },
            ],
        },
        {
            "id": "ml-exam-2",
            "title": "ML — Applied Assessment",
            "description": "Covers chapters 5–8: metrics, features, workflow, production.",
            "chapter_ids": ["evaluation", "features", "workflow", "production"],
            "questions": [
                {
                    "type": "scenario", "topic": "Metrics", "chapter_id": "evaluation",
                    "prompt": "A fraud model reports 99% accuracy on data where 1% of transactions are fraudulent. What does that tell you?",
                    "options": [
                        "It is an excellent model",
                        "Almost nothing — predicting 'never fraud' also scores 99%",
                        "The model is overfitting",
                        "The test set is too small",
                    ],
                    "answer": 1,
                    "explanation": "Accuracy is uninformative under class imbalance. Look at precision, recall and PR-AUC.",
                },
                {
                    "type": "mcq", "topic": "Metrics", "chapter_id": "evaluation",
                    "prompt": "For disease screening, where a missed case is far worse than a false alarm, which metric should you prioritise?",
                    "options": ["Precision", "Recall", "Accuracy", "Specificity"],
                    "answer": 1,
                    "explanation": "Recall measures how many true positives you found. Missing a case is the expensive error here.",
                },
                {
                    "type": "mcq", "topic": "Metrics", "chapter_id": "evaluation",
                    "prompt": "What does it mean for a classifier to be well calibrated?",
                    "options": [
                        "Its accuracy exceeds 90%",
                        "Among predictions of 0.8, roughly 80% are actually positive",
                        "It has equal precision and recall",
                        "Its features are scaled",
                    ],
                    "answer": 1,
                    "explanation": "Calibration matters whenever a downstream decision uses the probability as a probability.",
                },
                {
                    "type": "code", "topic": "Features", "chapter_id": "features", "language": "python",
                    "prompt": "What is wrong with this preprocessing?",
                    "code": "scaler = StandardScaler().fit(X)          # X = all data\nX_scaled = scaler.transform(X)\nX_train, X_test = split(X_scaled)",
                    "options": [
                        "StandardScaler is the wrong scaler",
                        "The scaler saw the test data, leaking its statistics into training",
                        "The split should come first only for time series",
                        "Nothing is wrong",
                    ],
                    "answer": 1,
                    "explanation": "Fit on training data only, inside a pipeline. This is the most common leak in beginner code.",
                },
                {
                    "type": "mcq", "topic": "Features", "chapter_id": "features",
                    "prompt": "Which technique is a classic source of target leakage if applied outside cross-validation folds?",
                    "options": ["One-hot encoding", "Target encoding", "Median imputation", "Min-max scaling"],
                    "answer": 1,
                    "explanation": "Replacing a category with the mean target uses label information; computed on the whole dataset it leaks directly.",
                },
                {
                    "type": "truefalse", "topic": "Features", "chapter_id": "features",
                    "prompt": "When handling class imbalance, you should resample the validation and test sets too, for consistency.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Evaluation sets must reflect the real distribution or your metrics describe a world that does not exist.",
                },
                {
                    "type": "mcq", "topic": "Workflow", "chapter_id": "workflow",
                    "prompt": "Why is a trivial baseline important?",
                    "options": [
                        "It is required by scikit-learn",
                        "It sets the bar a model must clear to be worth deploying, and reveals whether the data has signal",
                        "It speeds up training",
                        "It replaces the need for a test set",
                    ],
                    "answer": 1,
                    "explanation": "A model that beats 'predict the majority class' by one point is not worth its operational cost.",
                },
                {
                    "type": "scenario", "topic": "Workflow", "chapter_id": "workflow",
                    "prompt": "You have a fixed budget of 40 training runs for hyperparameter search over 6 parameters. What is the better strategy?",
                    "options": [
                        "Grid search over a coarse grid",
                        "Random search, since most hyperparameters matter little and grid search spends equal effort on all",
                        "Tune one parameter at a time to completion",
                        "Skip tuning entirely",
                    ],
                    "answer": 1,
                    "explanation": "Random search covers the important dimensions far better for the same budget.",
                },
                {
                    "type": "mcq", "topic": "Production", "chapter_id": "production",
                    "prompt": "A model's accuracy degrades over months although nothing about it changed. What is the likely cause?",
                    "options": [
                        "The model file corrupted",
                        "Data or concept drift — the world moved, the model did not",
                        "Overfitting appearing later",
                        "The learning rate decayed",
                    ],
                    "answer": 1,
                    "explanation": "Input distributions or the input→target relationship shift over time. Monitor distributions and retrain.",
                },
                {
                    "type": "scenario", "topic": "Production", "chapter_id": "production",
                    "prompt": "Offline validation shows 0.91 AUC, but the deployed model produces visibly worse predictions and no errors are logged. What should you check first?",
                    "options": [
                        "Whether the server has enough RAM",
                        "Train/serve skew — whether inference computes features exactly as training did",
                        "Whether the AUC was computed correctly",
                        "Whether the model needs more epochs",
                    ],
                    "answer": 1,
                    "explanation": "Silent degradation with no errors is the signature of feature computation diverging between the two paths.",
                },
            ],
        },
    ],
}
