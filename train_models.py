from app.model_train import train_models_for_all_items
res = train_models_for_all_items(min_samples=30)
for r in res:
    print(f"item_id={r.item_id}  n={r.n_samples}  cv_mape={r.cv_mape}  saved={r.saved}")
